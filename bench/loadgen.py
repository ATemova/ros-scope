"""rosscope benchmark harness.

Floods the telemetry pipeline and measures two things that back up the
"separates ingestion from serving / scales independently" claim with numbers:

  throughput  — publish N samples to the Redis stream as fast as possible,
                then watch them land in TimescaleDB and report ingest rate.
  latency     — publish paced samples tagged with their produce time, poll the
                DB as rows arrive, and report produce -> queryable latency
                (mean / p50 / p95 / p99).

Run it inside the compose network (it needs to reach `redis` and `db`):

    docker compose --profile bench run --rm bench --count 100000 --latency-samples 500

Results print as JSON so they're easy to paste into the README.
"""
from __future__ import annotations

import argparse
import asyncio
import time

import asyncpg
import redis.asyncio as aioredis

from bench.stats import summarize
from common.schema import STREAM, scalar

THROUGHPUT_ROBOT = "bench-thru"
LATENCY_ROBOT = "bench-lat"


async def publish_throughput(r: aioredis.Redis, count: int) -> float:
    """Publish `count` samples as fast as possible; return wall seconds."""
    start = time.perf_counter()
    batch = 1000
    sent = 0
    while sent < count:
        pipe = r.pipeline(transaction=False)
        for i in range(sent, min(sent + batch, count)):
            s = scalar(THROUGHPUT_ROBOT, "/bench", {"seq": i}, ts=time.time())
            pipe.xadd(STREAM, {"data": s.to_json()}, maxlen=2_000_000, approximate=True)
        await pipe.execute()
        sent += batch
    return time.perf_counter() - start


async def wait_for_ingest(pool: asyncpg.Pool, robot: str, target: int, timeout_s: float) -> tuple[int, float]:
    """Poll until `target` rows for `robot` are stored (or timeout). Returns
    (rows_seen, seconds_from_first_publish_to_completion)."""
    start = time.perf_counter()
    seen = 0
    while seen < target and time.perf_counter() - start < timeout_s:
        seen = await pool.fetchval(
            "SELECT count(*) FROM telemetry WHERE robot_id=$1 AND metric='seq'", robot) or 0
        if seen < target:
            await asyncio.sleep(0.05)
    return seen, time.perf_counter() - start


async def measure_latency(r: aioredis.Redis, pool: asyncpg.Pool, n: int, rate_hz: float) -> list[float]:
    """Publish `n` paced samples, poll the DB as they arrive, and return the
    per-sample produce -> queryable latencies in milliseconds."""
    produced: dict[int, float] = {}
    period = 1.0 / rate_hz if rate_hz > 0 else 0.0

    async def producer():
        for i in range(n):
            now = time.time()
            produced[i] = now
            s = scalar(LATENCY_ROBOT, "/bench", {"lat_seq": i}, ts=now)
            await r.xadd(STREAM, {"data": s.to_json()}, maxlen=2_000_000, approximate=True)
            if period:
                await asyncio.sleep(period)

    prod_task = asyncio.create_task(producer())
    latencies: list[float] = []
    last_seen = -1
    deadline = time.perf_counter() + 60
    while last_seen < n - 1 and time.perf_counter() < deadline:
        max_seq = await pool.fetchval(
            "SELECT max(value) FROM telemetry WHERE robot_id=$1 AND metric='lat_seq'", LATENCY_ROBOT)
        if max_seq is not None:
            arrived = time.time()
            for seq in range(last_seen + 1, int(max_seq) + 1):
                if seq in produced:
                    latencies.append((arrived - produced[seq]) * 1000.0)
            last_seen = int(max_seq)
        await asyncio.sleep(0.02)
    await prod_task
    return latencies


async def cleanup(pool: asyncpg.Pool) -> None:
    await pool.execute("DELETE FROM telemetry WHERE robot_id LIKE 'bench-%'")


async def main() -> None:
    ap = argparse.ArgumentParser(description="rosscope pipeline benchmark")
    ap.add_argument("--redis-url", default="redis://redis:6379/0")
    ap.add_argument("--pg-dsn", default="postgresql://rosscope:rosscope@db:5432/rosscope")
    ap.add_argument("--count", type=int, default=50_000, help="throughput sample count")
    ap.add_argument("--latency-samples", type=int, default=500)
    ap.add_argument("--latency-rate", type=float, default=200.0, help="latency publish rate (Hz)")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    r = aioredis.from_url(args.redis_url)
    pool = await asyncpg.create_pool(args.pg_dsn, min_size=1, max_size=4)
    await cleanup(pool)

    print(f"[bench] publishing {args.count} samples for throughput...", flush=True)
    pub_s = await publish_throughput(r, args.count)
    seen, ingest_s = await wait_for_ingest(pool, THROUGHPUT_ROBOT, args.count, args.timeout)

    print(f"[bench] measuring latency over {args.latency_samples} samples...", flush=True)
    latencies = await measure_latency(r, pool, args.latency_samples, args.latency_rate)

    result = {
        "publish": {"count": args.count, "duration_s": round(pub_s, 3),
                    "publish_per_s": round(args.count / pub_s, 1) if pub_s else 0.0},
        "ingest": summarize([], seen, ingest_s) | {"rows_stored": seen},
        "end_to_end_latency": summarize(latencies, len(latencies),
                                        max(1e-9, len(latencies) / args.latency_rate)),
    }
    await cleanup(pool)
    await pool.close()
    await r.aclose()

    import json
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
