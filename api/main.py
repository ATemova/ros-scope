"""rosscope API.

Serves three things:
  1. REST history endpoints backed by TimescaleDB (robots, series, poses,
     alerts, topic health).
  2. A /ws/live WebSocket that tails the Redis stream for live telemetry and
     subscribes to the alerts channel — this is the serving side, kept fully
     separate from ingest.
  3. The static dashboard at /.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from api.metrics import render_prometheus
from common.log import get_logger
from common.schema import ALERTS_CHANNEL, STREAM, Sample

log = get_logger("api")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
PG_DSN = os.environ.get("PG_DSN", "postgresql://rosscope:rosscope@db:5432/rosscope")

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    for _ in range(30):
        try:
            state["pool"] = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=8)
            break
        except (OSError, asyncpg.PostgresError):
            await asyncio.sleep(2)
    state["redis"] = aioredis.from_url(REDIS_URL)
    # Ensure the sessions table exists even on a pre-existing database volume.
    async with state["pool"].acquire() as con:
        await con.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                   id BIGSERIAL PRIMARY KEY,
                   name TEXT NOT NULL,
                   started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                   ended_at TIMESTAMPTZ)""")
    log.info("api ready")
    yield
    await state["pool"].close()
    await state["redis"].aclose()


app = FastAPI(title="rosscope", version="0.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------------- #
@app.get("/api/robots")
async def robots():
    rows = await state["pool"].fetch(
        "SELECT robot_id, label, first_seen, last_seen FROM robots ORDER BY robot_id")
    return [dict(r) for r in rows]


@app.get("/api/topics")
async def topics(robot_id: str):
    rows = await state["pool"].fetch(
        """SELECT DISTINCT topic, metric FROM telemetry
           WHERE robot_id=$1 AND time > now() - interval '5 minutes'
           ORDER BY topic, metric""", robot_id)
    return [dict(r) for r in rows]


@app.get("/api/series")
async def series(robot_id: str, metric: str,
                 minutes: int = Query(5, ge=1, le=720)):
    """Recent samples for one metric. Uses the 1-second rollup for long
    windows so the payload stays small; raw rows for short windows."""
    pool = state["pool"]
    if minutes <= 10:
        rows = await pool.fetch(
            """SELECT time, value FROM telemetry
               WHERE robot_id=$1 AND metric=$2 AND time > now() - ($3 || ' minutes')::interval
               ORDER BY time""", robot_id, metric, str(minutes))
        return [{"t": r["time"].timestamp(), "v": r["value"]} for r in rows]
    rows = await pool.fetch(
        """SELECT bucket AS time, avg_value AS value FROM telemetry_1s
           WHERE robot_id=$1 AND metric=$2 AND bucket > now() - ($3 || ' minutes')::interval
           ORDER BY bucket""", robot_id, metric, str(minutes))
    return [{"t": r["time"].timestamp(), "v": r["value"]} for r in rows]


@app.get("/api/poses")
async def poses(robot_id: str, seconds: int = Query(60, ge=1, le=600)):
    rows = await state["pool"].fetch(
        """SELECT time, x, y, z, qx, qy, qz, qw FROM poses
           WHERE robot_id=$1 AND time > now() - ($2 || ' seconds')::interval
           ORDER BY time""", robot_id, str(seconds))
    return [{"t": r["time"].timestamp(), **{k: r[k] for k in ("x","y","z","qx","qy","qz","qw")}}
            for r in rows]


@app.get("/api/alerts")
async def alerts(limit: int = Query(50, ge=1, le=500)):
    rows = await state["pool"].fetch(
        "SELECT time, robot_id, topic, rule, severity, message, value FROM alerts "
        "ORDER BY time DESC LIMIT $1", limit)
    return [dict(r) | {"time": r["time"].timestamp()} for r in rows]


@app.get("/api/summary")
async def summary():
    """Fleet KPIs for the dashboard header: robots online (telemetry in the
    last 10s), active alerts (last 5 min), and the lowest battery voltage."""
    pool = state["pool"]
    online = await pool.fetchval(
        "SELECT count(DISTINCT robot_id) FROM telemetry WHERE time > now() - interval '10 seconds'")
    active_alerts = await pool.fetchval(
        "SELECT count(*) FROM alerts WHERE time > now() - interval '5 minutes'")
    min_batt = await pool.fetchval(
        """SELECT min(value) FROM telemetry
           WHERE metric='voltage' AND time > now() - interval '30 seconds'""")
    return {"robots_online": online or 0,
            "active_alerts": active_alerts or 0,
            "min_voltage": round(min_batt, 2) if min_batt is not None else None}


# --------------------------------------------------------------------------- #
# Sessions: record (bookmark a time range) and replay
# --------------------------------------------------------------------------- #
@app.post("/api/sessions/start")
async def session_start(body: dict):
    name = (body or {}).get("name") or "session"
    sid = await state["pool"].fetchval(
        "INSERT INTO sessions (name) VALUES ($1) RETURNING id", name)
    log.info("session %s started (%s)", sid, name)
    return {"id": sid, "name": name}


@app.post("/api/sessions/{sid}/stop")
async def session_stop(sid: int):
    await state["pool"].execute(
        "UPDATE sessions SET ended_at = now() WHERE id = $1 AND ended_at IS NULL", sid)
    return {"id": sid, "stopped": True}


@app.get("/api/sessions")
async def session_list():
    rows = await state["pool"].fetch(
        """SELECT s.id, s.name, s.started_at, s.ended_at,
                  EXTRACT(EPOCH FROM (COALESCE(s.ended_at, now()) - s.started_at)) AS seconds
           FROM sessions s ORDER BY s.started_at DESC LIMIT 50""")
    return [{"id": r["id"], "name": r["name"],
             "start": r["started_at"].timestamp(),
             "end": r["ended_at"].timestamp() if r["ended_at"] else None,
             "seconds": round(r["seconds"], 1)} for r in rows]


@app.delete("/api/sessions/{sid}")
async def session_delete(sid: int):
    await state["pool"].execute("DELETE FROM sessions WHERE id = $1", sid)
    return {"id": sid, "deleted": True}


@app.get("/api/sessions/{sid}/data")
async def session_data(sid: int):
    """Full replay payload for a session: pose trails (~2 Hz), the headline
    metric series (~1 Hz), and alerts — all bounded to the session's window
    and downsampled so the scrubber stays smooth."""
    pool = state["pool"]
    s = await pool.fetchrow("SELECT id, name, started_at, ended_at FROM sessions WHERE id = $1", sid)
    if not s:
        return JSONResponse({"error": "not found"}, status_code=404)
    start = s["started_at"]
    end = s["ended_at"] or datetime_now()

    pose_rows = await pool.fetch(
        """SELECT robot_id, time_bucket('0.5 seconds', time) AS t,
                  avg(x) AS x, avg(y) AS y, avg(z) AS z,
                  last(qz, time) AS qz, last(qw, time) AS qw
           FROM poses WHERE time BETWEEN $1 AND $2
           GROUP BY robot_id, t ORDER BY robot_id, t""", start, end)
    series_rows = await pool.fetch(
        """SELECT robot_id, metric, time_bucket('1 second', time) AS t, avg(value) AS v
           FROM telemetry WHERE metric = ANY($3::text[]) AND time BETWEEN $1 AND $2
           GROUP BY robot_id, metric, t ORDER BY t""",
        start, end, ["voltage", "cpu_temp"])
    alert_rows = await pool.fetch(
        """SELECT time, robot_id, topic, rule, severity, message FROM alerts
           WHERE time BETWEEN $1 AND $2 ORDER BY time""", start, end)

    poses: dict = {}
    for r in pose_rows:
        poses.setdefault(r["robot_id"], []).append(
            {"t": r["t"].timestamp(), "x": r["x"], "y": r["y"], "z": r["z"],
             "qz": r["qz"], "qw": r["qw"]})
    series: dict = {}
    for r in series_rows:
        series.setdefault(r["robot_id"], {}).setdefault(r["metric"], []).append(
            {"t": r["t"].timestamp(), "v": r["v"]})
    alerts_out = [{"t": r["time"].timestamp(), "robot_id": r["robot_id"], "topic": r["topic"],
                   "rule": r["rule"], "severity": r["severity"], "message": r["message"]}
                  for r in alert_rows]

    return {"id": s["id"], "name": s["name"],
            "start": start.timestamp(), "end": end.timestamp(),
            "robots": sorted(poses.keys() | series.keys()),
            "poses": poses, "series": series, "alerts": alerts_out}


def datetime_now():
    from datetime import datetime
    return datetime.now(UTC)


@app.get("/api/health")
async def health():
    """Per (robot, topic) last-seen + observed rate over the last 10s.
    This powers the topic-health strip on the dashboard."""
    rows = await state["pool"].fetch(
        """SELECT robot_id, topic,
                  max(time) AS last_seen,
                  count(*) FILTER (WHERE time > now() - interval '10 seconds') / 10.0 AS hz
           FROM telemetry
           WHERE time > now() - interval '1 minute'
           GROUP BY robot_id, topic ORDER BY robot_id, topic""")
    return [{"robot_id": r["robot_id"], "topic": r["topic"],
             "last_seen": r["last_seen"].timestamp(), "hz": round(r["hz"], 1)} for r in rows]


# --------------------------------------------------------------------------- #
# Live WebSocket: telemetry (stream tail) + alerts (pub/sub)
# --------------------------------------------------------------------------- #
@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    r = state["redis"]
    last_id = "$"
    pubsub = r.pubsub()
    await pubsub.subscribe(ALERTS_CHANNEL)
    try:
        while True:
            # alerts first (non-blocking)
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.01)
            if msg and msg["type"] == "message":
                await ws.send_json({"type": "alert", "data": json.loads(msg["data"])})

            resp = await r.xread({STREAM: last_id}, count=200, block=200)
            for _stream, entries in resp or []:
                for msg_id, fields in entries:
                    last_id = msg_id
                    s = Sample.from_json(fields[b"data"])
                    await ws.send_json({"type": "sample", "data": {
                        "robot_id": s.robot_id, "topic": s.topic, "kind": s.kind,
                        "ts": s.ts, "metrics": s.metrics, "pose": s.pose}})
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(ALERTS_CHANNEL)
        await pubsub.aclose()


@app.get("/metrics")
async def metrics():
    """Prometheus exposition of rosscope's own state — scrape this and graph it
    in Grafana. The observability platform is itself observable."""
    pool = state["pool"]
    online = await pool.fetchval(
        "SELECT count(DISTINCT robot_id) FROM telemetry WHERE time > now() - interval '10 seconds'")
    samples_1m = await pool.fetchval(
        "SELECT count(*) FROM telemetry WHERE time > now() - interval '1 minute'")
    active_alerts = await pool.fetchval(
        "SELECT count(*) FROM alerts WHERE time > now() - interval '5 minutes'")
    anomaly_1h = await pool.fetchval(
        "SELECT count(*) FROM alerts WHERE rule='anomaly' AND time > now() - interval '1 hour'")
    min_batt = await pool.fetchval(
        "SELECT min(value) FROM telemetry WHERE metric='voltage' AND time > now() - interval '30 seconds'")
    sessions = await pool.fetchval("SELECT count(*) FROM sessions")
    body = render_prometheus([
        ("rosscope_robots_online", "gauge", "Robots reporting telemetry in the last 10s", online or 0),
        ("rosscope_samples_1m", "gauge", "Telemetry samples ingested in the last minute", samples_1m or 0),
        ("rosscope_active_alerts", "gauge", "Alerts raised in the last 5 minutes", active_alerts or 0),
        ("rosscope_anomaly_alerts_1h", "gauge", "Anomaly alerts in the last hour", anomaly_1h or 0),
        ("rosscope_min_battery_volts", "gauge", "Lowest battery voltage in the last 30s", min_batt),
        ("rosscope_sessions_total", "gauge", "Recorded sessions", sessions or 0),
    ])
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True})


# Static dashboard (mounted last so /api and /ws win).
app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True),
          name="static")