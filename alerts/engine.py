"""Alert engine.

Evaluates two kinds of rules against the live telemetry stream:

  - threshold rules  (battery low, cpu overheat ...) checked per sample
  - staleness rules  (a topic stops arriving) checked by a background sweep
    against the last time each (robot, topic) was seen

Raised alerts are written to the `alerts` table and published on a Redis
Pub/Sub channel so the API can push them to the dashboard in real time. The
pure rule logic lives in `evaluate_sample` / `evaluate_staleness` with no I/O,
so it is unit-tested directly in tests/test_rules.py.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from alerts.anomaly import AnomalyDetector
from alerts.detector import LearnedDetector, load_model
from common.log import get_logger

# Note: asyncpg / redis / yaml are imported lazily inside the functions that
# use them, so the pure rule logic (Cooldowns, evaluate_sample,
# evaluate_staleness) can be imported and unit-tested with no infra deps.
from common.schema import ALERTS_CHANNEL, STREAM, Sample

log = get_logger("alerts")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
PG_DSN = os.environ.get("PG_DSN", "postgresql://rosscope:rosscope@db:5432/rosscope")
RULES_PATH = os.environ.get("RULES_PATH", "/app/alerts/rules.yaml")
GROUP = "alerts"
CONSUMER = os.environ.get("HOSTNAME", "alerts-1")


@dataclass
class Alert:
    robot_id: str
    topic: str | None
    rule: str
    severity: str
    message: str
    value: float | None = None


class Cooldowns:
    """Suppress repeat alerts for the same (rule, robot) within cooldown_s."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], float] = {}

    def ready(self, rule: str, robot: str, cooldown_s: float, now: float) -> bool:
        key = (rule, robot)
        if now - self._last.get(key, -1e9) >= cooldown_s:
            self._last[key] = now
            return True
        return False


def evaluate_sample(sample: Sample, thresholds: list[dict], cd: Cooldowns, now: float) -> list[Alert]:
    """Pure threshold check for a single scalar sample."""
    out: list[Alert] = []
    if sample.kind != "scalar":
        return out
    for rule in thresholds:
        metric = rule["metric"]
        if metric not in sample.metrics:
            continue
        v = sample.metrics[metric]
        hit = (rule["op"] == "lt" and v < rule["bound"]) or \
              (rule["op"] == "gt" and v > rule["bound"])
        if hit and cd.ready(rule["name"], sample.robot_id, rule.get("cooldown_s", 30), now):
            out.append(Alert(sample.robot_id, sample.topic, rule["name"],
                             rule["severity"], f"{rule['message']} ({v})", v))
    return out


def evaluate_staleness(last_seen: dict[tuple[str, str], float], staleness: list[dict],
                       cd: Cooldowns, now: float) -> list[Alert]:
    """Pure staleness check across all tracked (robot, topic) pairs."""
    out: list[Alert] = []
    for rule in staleness:
        topic = rule["topic"]
        for (robot, t), ts in last_seen.items():
            if t != topic:
                continue
            if now - ts > rule["timeout_s"] and \
               cd.ready(rule["name"], robot, rule.get("cooldown_s", 15), now):
                gap = round(now - ts, 1)
                out.append(Alert(robot, topic, rule["name"], rule["severity"],
                                 f"{rule['message']} ({gap}s gap)", gap))
    return out


async def persist_and_publish(pool, r, alert: Alert) -> None:
    async with pool.acquire() as con:
        await con.execute(
            """INSERT INTO alerts (robot_id, topic, rule, severity, message, value)
               VALUES ($1,$2,$3,$4,$5,$6)""",
            alert.robot_id, alert.topic, alert.rule, alert.severity, alert.message, alert.value)
    import json
    await r.publish(ALERTS_CHANNEL, json.dumps({
        "robot_id": alert.robot_id, "topic": alert.topic, "rule": alert.rule,
        "severity": alert.severity, "message": alert.message,
        "value": alert.value, "ts": time.time(),
    }))


async def staleness_loop(pool, r, staleness, last_seen, cd) -> None:
    while True:
        await asyncio.sleep(1.0)
        for alert in evaluate_staleness(last_seen, staleness, cd, time.time()):
            await persist_and_publish(pool, r, alert)


def _build_detector(acfg: dict):
    """Pick the anomaly detector: a frozen trained model when method=learned and
    the model file loads, otherwise the online rolling detector."""
    cooldown = acfg.get("cooldown_s", 20)
    if acfg.get("method") == "learned":
        path = acfg.get("model_path", "alerts/model.json")
        try:
            model = load_model(path)
            log.info("anomaly: learned model %s (threshold %.2f)", path, model["threshold"])
            return LearnedDetector(model, cooldown_s=cooldown)
        except (OSError, KeyError, ValueError) as e:
            log.warning("anomaly: could not load learned model (%s); using rolling", e)
    return AnomalyDetector(
        features=acfg.get("features", ["voltage", "cpu_temp", "yaw_rate"]),
        window=acfg.get("window", 240), warmup=acfg.get("warmup", 60),
        threshold=acfg.get("threshold", 4.0), cooldown_s=cooldown)


async def main() -> None:
    import redis.asyncio as aioredis
    import yaml

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)
    thresholds = rules.get("thresholds", [])
    staleness = rules.get("staleness", [])
    acfg = rules.get("anomaly", {}) or {}
    detector = None
    if acfg.get("enabled"):
        detector = _build_detector(acfg)
    anomaly_severity = acfg.get("severity", "warning")

    r = aioredis.from_url(REDIS_URL)
    pool = await _connect_pg()
    try:
        await r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    cd = Cooldowns()
    last_seen: dict[tuple[str, str], float] = {}
    asyncio.create_task(staleness_loop(pool, r, staleness, last_seen, cd))
    log.info("%d thresholds, %d staleness rules, anomaly=%s",
             len(thresholds), len(staleness), bool(detector))

    while True:
        resp = await r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=100, block=1000)
        ids = []
        for _stream, entries in resp or []:
            for msg_id, fields in entries:
                ids.append(msg_id)
                s = Sample.from_json(fields[b"data"])
                now = time.time()
                last_seen[(s.robot_id, s.topic)] = s.ts
                for alert in evaluate_sample(s, thresholds, cd, now):
                    await persist_and_publish(pool, r, alert)
                if detector and s.kind == "scalar":
                    for metric, value in s.metrics.items():
                        hit = detector.update(s.robot_id, metric, value, now)
                        if hit:
                            feats = ", ".join(f"{k}={v}" for k, v in hit["features"].items())
                            await persist_and_publish(pool, r, Alert(
                                s.robot_id, None, "anomaly", anomaly_severity,
                                f"Unusual sensor pattern (score {hit['score']}): {feats}",
                                hit["score"]))
        if ids:
            await r.xack(STREAM, GROUP, *ids)


async def _connect_pg():
    import asyncpg
    for _ in range(30):
        try:
            return await asyncpg.create_pool(PG_DSN, min_size=1, max_size=2)
        except (OSError, asyncpg.PostgresError):
            log.info("waiting for postgres...")
            await asyncio.sleep(2)
    raise RuntimeError("could not connect to postgres")


if __name__ == "__main__":
    asyncio.run(main())
