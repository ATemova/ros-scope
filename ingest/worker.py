"""Ingestion worker.

Consumes the telemetry stream with a Redis consumer group (so storage is
durable and survives a restart without losing un-acked samples) and writes
batched rows into TimescaleDB. Running this as its own process is the whole
point of the architecture: ingest scales and fails independently of the API
that serves the dashboard.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime

import asyncpg
import redis.asyncio as aioredis

from common.log import get_logger
from common.schema import STREAM, Sample

log = get_logger("ingest")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
PG_DSN = os.environ.get("PG_DSN", "postgresql://rosscope:rosscope@db:5432/rosscope")
GROUP = "ingest"
CONSUMER = os.environ.get("HOSTNAME", "ingest-1")
BATCH = int(os.environ.get("INGEST_BATCH", "200"))
FLUSH_MS = int(os.environ.get("INGEST_FLUSH_MS", "500"))


async def ensure_group(r: aioredis.Redis) -> None:
    try:
        await r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise  # group already exists -> fine


async def flush(pool: asyncpg.Pool, tele: list, poses: list, seen: dict) -> None:
    if not (tele or poses or seen):
        return
    async with pool.acquire() as con:
        async with con.transaction():
            if tele:
                await con.copy_records_to_table(
                    "telemetry", records=tele,
                    columns=["time", "robot_id", "topic", "metric", "value"])
            if poses:
                await con.copy_records_to_table(
                    "poses", records=poses,
                    columns=["time", "robot_id", "x", "y", "z", "qx", "qy", "qz", "qw"])
            if seen:
                await con.executemany(
                    """INSERT INTO robots (robot_id, label, last_seen)
                       VALUES ($1, $1, to_timestamp($2))
                       ON CONFLICT (robot_id)
                       DO UPDATE SET last_seen = EXCLUDED.last_seen""",
                    [(rid, ts) for rid, ts in seen.items()])


async def upsert_map(pool: asyncpg.Pool, s: Sample) -> None:
    """Maps are single current artifacts, so upsert the latest (not batched)."""
    m = s.map
    await pool.execute(
        """INSERT INTO maps (robot_id, resolution, width, height, origin_x, origin_y, data, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, now())
           ON CONFLICT (robot_id) DO UPDATE SET
               resolution = EXCLUDED.resolution, width = EXCLUDED.width,
               height = EXCLUDED.height, origin_x = EXCLUDED.origin_x,
               origin_y = EXCLUDED.origin_y, data = EXCLUDED.data, updated_at = now()""",
        s.robot_id, float(m.get("resolution", 0)), int(m.get("width", 0)), int(m.get("height", 0)),
        float(m.get("origin_x", 0)), float(m.get("origin_y", 0)), json.dumps(m.get("data", [])))


CLAIM_IDLE_MS = int(os.environ.get("INGEST_CLAIM_IDLE_MS", "30000"))


async def reclaim_pending(r: aioredis.Redis) -> list:
    """Take over entries delivered to a consumer that died before acking them
    (e.g. a previous container with a different HOSTNAME), so a restart never
    strands samples in the pending list."""
    claimed: list = []
    cursor = "0-0"
    while True:
        cursor, entries, *_ = await r.xautoclaim(STREAM, GROUP, CONSUMER, CLAIM_IDLE_MS, cursor, count=BATCH)
        claimed.extend(e for e in entries if e and e[1])
        if cursor in (b"0-0", "0-0"):
            return claimed


def to_ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


async def main() -> None:
    r = aioredis.from_url(REDIS_URL)
    pool = await connect_pg()
    await ensure_group(r)
    log.info("consumer=%s batch=%s -> %s", CONSUMER, BATCH, PG_DSN.split("@")[-1])

    tele: list = []
    poses: list = []
    seen: dict[str, float] = {}
    pending_acks: list = []
    last_flush = time.monotonic()

    async def handle(entries) -> None:
        for msg_id, fields in entries:
            pending_acks.append(msg_id)
            s = Sample.from_json(fields[b"data"])
            seen[s.robot_id] = max(seen.get(s.robot_id, 0.0), s.ts)
            ts = to_ts(s.ts)
            if s.kind == "scalar":
                for metric, value in s.metrics.items():
                    tele.append((ts, s.robot_id, s.topic, metric, value))
            elif s.kind == "pose":
                p = s.pose
                poses.append((ts, s.robot_id, p["x"], p["y"], p["z"],
                              p["qx"], p["qy"], p["qz"], p["qw"]))
            elif s.kind == "map":
                await upsert_map(pool, s)
            # kind == "scan": live-only, forwarded by the API, never stored

    reclaimed = await reclaim_pending(r)
    if reclaimed:
        log.info("reclaimed %d pending entries", len(reclaimed))
        await handle(reclaimed)

    while True:
        resp = await r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=BATCH, block=FLUSH_MS)
        for _stream, entries in resp or []:
            await handle(entries)

        due = (time.monotonic() - last_flush) * 1000 >= FLUSH_MS
        if pending_acks and (len(tele) + len(poses) >= BATCH or due or not resp):
            # Ack only after the rows are committed: a crash before this point
            # leaves the entries pending, and reclaim_pending() replays them.
            await flush(pool, tele, poses, seen)
            await r.xack(STREAM, GROUP, *pending_acks)
            tele.clear()
            poses.clear()
            seen.clear()
            pending_acks.clear()
            last_flush = time.monotonic()


async def connect_pg() -> asyncpg.Pool:
    last = None
    for _ in range(30):
        try:
            return await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
        except (OSError, asyncpg.PostgresError) as e:
            last = e
            log.info("waiting for postgres...")
            await asyncio.sleep(2)
    raise RuntimeError(f"could not connect to postgres: {last}")


if __name__ == "__main__":
    asyncio.run(main())
