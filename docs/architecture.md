# Architecture

```
                 ┌──────────────┐        ┌──────────────┐
   real robot ──▶│  ROS 2 bridge│        │  synthetic   │◀── no hardware
   or Gazebo     │  (rclpy)     │        │  publisher   │     needed
                 └──────┬───────┘        └──────┬───────┘
                        │   same envelope        │
                        ▼                        ▼
                   ┌─────────────────────────────────┐
                   │      Redis Stream  "telemetry"   │   durable buffer
                   └───────┬──────────────────┬───────┘
            consumer group │                  │ consumer group
                           ▼                  ▼
                   ┌──────────────┐   ┌──────────────┐
                   │   ingest     │   │   alerts     │── Redis Pub/Sub ─┐
                   │  (batched)   │   │ (rules+stale)│   "alerts"       │
                   └──────┬───────┘   └──────┬───────┘                  │
                          ▼                  ▼                          │
                   ┌─────────────────────────────────┐                 │
                   │          TimescaleDB             │                 │
                   │  telemetry · poses · alerts      │                 │
                   │  + 1s continuous aggregate       │                 │
                   └──────────────┬──────────────────┘                 │
                                  │ REST history                       │
                                  ▼                                    │
                   ┌─────────────────────────────────┐                 │
                   │            FastAPI               │◀── stream tail ─┘
                   │  REST  +  /ws/live  +  static    │   (live fan-out)
                   └──────────────┬──────────────────┘
                                  ▼
                   ┌─────────────────────────────────┐
                   │   dashboard (3D pose · charts ·  │
                   │   topic health · alert feed)     │
                   └─────────────────────────────────┘
```

## Why it is shaped this way

**Producers are interchangeable.** The synthetic publisher and the ROS 2
bridge emit the *same* envelope (`common/schema.py`) into the same stream.
Storage, alerting, and the dashboard never know which one is running, so the
project demos with zero hardware yet runs unchanged against a real robot.

**Ingestion is separate from serving.** A naive version pushes every sample
through an HTTP endpoint into the database. That couples write throughput to
the web tier and falls over under sensor-rate traffic. Here a Redis Stream
absorbs bursts, a dedicated `ingest` worker drains it with a durable consumer
group and batched `COPY` inserts, and the API only *reads*. Each side scales
and fails independently.

**Live and historical are different paths.** The dashboard gets live data by
tailing the Redis stream over a WebSocket (cheap, no DB round-trip), and gets
history from TimescaleDB. Long time ranges are served from a 1-second
continuous aggregate instead of raw rows, so a multi-hour chart stays small.

**Alerting reads the same stream.** The alert engine consumes telemetry with
its own consumer group, evaluates threshold rules per sample and staleness
rules on a background sweep, then writes to the `alerts` table and publishes on
a Pub/Sub channel the API relays to the browser. The rule logic is pure and
unit-tested in `tests/test_rules.py`.

## Components

| Service   | Role                                                        |
|-----------|-------------------------------------------------------------|
| `sim`     | synthetic fleet → Redis stream (default demo source)        |
| `bridge`  | ROS 2 topics → Redis stream (compose profile `ros`)         |
| `ingest`  | stream → batched writes into TimescaleDB                     |
| `alerts`  | rule + staleness engine → alerts table + Pub/Sub            |
| `api`     | REST history, `/ws/live`, serves the dashboard              |
| `db`      | TimescaleDB (hypertables, continuous aggregate, retention)  |
| `redis`   | stream buffer + alert Pub/Sub                               |
