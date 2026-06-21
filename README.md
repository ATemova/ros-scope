# 📡 rosscope

**Live telemetry, health, and 3D pose for ROS 2 robot fleets — runnable with one command, no robot required.**

---

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![ROS 2](https://img.shields.io/badge/ROS%202-Humble-22314E?logo=ros&logoColor=white)
![Backend](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)
![Storage](https://img.shields.io/badge/Storage-TimescaleDB-FDB515?logo=timescale&logoColor=white)
![Streaming](https://img.shields.io/badge/Streaming-Redis%20Streams-DC382D?logo=redis&logoColor=white)
![Frontend](https://img.shields.io/badge/Frontend-Three.js%20%7C%20uPlot-049EF4?logo=threedotjs&logoColor=white)
![Infra](https://img.shields.io/badge/Infra-Docker%20Compose-2496ED?logo=docker&logoColor=white)
![Live](https://img.shields.io/badge/Live-WebSocket%20Stream-FF8A3D)
![Alerts](https://img.shields.io/badge/Alerts-Threshold%20%2B%20Staleness-E4574C)
![Demo](https://img.shields.io/badge/Demo-No%20Hardware%20Needed-5FB98E)
[![CI](https://img.shields.io/github/actions/workflow/status/ATemova/ros-scope/ci.yml?branch=main&logo=githubactions&logoColor=white&label=CI)](https://github.com/ATemova/ros-scope/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/License-MIT-3DA639)

rosscope is a production-style observability platform for robot fleets. It bridges ROS 2 topics into a time-series database and serves a live dashboard with 3D pose, signal charts, per-topic health, and threshold + staleness alerts. The whole stack comes up with `docker compose up` and streams a synthetic fleet immediately — so you can try it without ROS installed and without hardware — then runs unchanged against a real robot via the ROS 2 bridge.

<!-- Replace with a screen recording of the running dashboard. The hero GIF is
     the single most important element of this README — record ~10s showing the
     3D trails moving, a chart updating, and an alert landing. -->
![rosscope dashboard](docs/dashboard.gif)

## What it does

- **Live 3D pose** for the whole fleet with per-robot trajectory trails, fed by odometry over a WebSocket.
- **Streaming signal charts** (battery, CPU temperature, IMU) with history backed by TimescaleDB.
- **Topic health strip** showing the observed rate of each topic and flagging the moment one goes stale.
- **Alerting** on thresholds (battery low/critical, CPU overheat) and on missing data (a sensor topic that stops arriving), pushed live to the dashboard.
- **Session record & replay**: bookmark a time range, then scrub through it on a timeline (play/pause/seek/speed) with the whole dashboard — 3D trails, charts, alerts — replaying from stored data.

## Architecture

```
producers          buffer            workers              storage           serving
─────────          ──────            ───────              ───────           ───────
ROS 2 bridge  ─┐                 ┌─ ingest  ─┐
               ├▶ Redis Stream ──┤            ├▶ TimescaleDB ──▶ FastAPI ──▶ dashboard
synthetic sim ─┘   "telemetry"   └─ alerts  ─┘   (+ 1s rollup)   REST + WS    (3D · charts ·
                                     │                            ▲            health · alerts)
                                     └── Redis Pub/Sub "alerts" ──┘
```

The design decision worth calling out: **ingestion is separated from serving.** A Redis Stream absorbs sensor-rate bursts, a dedicated worker drains it with batched inserts, and the API only reads — so write throughput and the web tier scale independently. Full rationale in [`docs/architecture.md`](docs/architecture.md).

## Tech stack

| Layer      | Tools |
|------------|-------|
| Backend    | FastAPI, Uvicorn, asyncpg |
| Storage    | TimescaleDB (hypertables, continuous aggregates, retention) |
| Streaming  | Redis Streams (pipeline) + Redis Pub/Sub (alerts) |
| Robotics   | ROS 2 Humble, rclpy, standard `sensor_msgs` / `nav_msgs` |
| Frontend   | Three.js (3D pose), µPlot (charts), vanilla ES — no build step |
| Infra      | Docker Compose, multi-service, health-gated startup |

## Quickstart

No robot and no ROS install required — the default stack runs a synthetic fleet.

```bash
git clone https://github.com/ATemova/ros-scope.git
cd rosscope
docker compose up --build
```

Open **http://localhost:8000**. Within a few seconds you'll see three robots streaming, trails drawing in 3D, and the first alerts arriving as the simulated batteries drain and one robot's `/scan` topic drops out.

Run the unit tests for the alert engine (no containers needed):

```bash
pip install pytest pyyaml && python -m pytest -q tests
```

### Feeding real ROS 2 data

The `ros` profile starts the rclpy bridge plus a small demo publisher so you can verify the ROS path end to end:

```bash
docker compose --profile ros up --build
```

The bridge subscribes to `/battery_state`, `/imu`, `/odom`, and `/diagnostics` and forwards them into the same pipeline. Point it at your own robot or a Gazebo bringup by replacing the demo publisher.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/summary` | Fleet KPIs: robots online, active alerts, lowest battery |
| GET | `/api/robots` | Known robots with first/last-seen timestamps |
| GET | `/api/topics?robot_id=` | Topics & metrics seen for a robot |
| GET | `/api/series?robot_id=&metric=&minutes=` | Metric history (raw, or 1s rollup for long windows) |
| GET | `/api/poses?robot_id=&seconds=` | Recent pose samples |
| GET | `/api/alerts?limit=` | Most recent alerts |
| GET | `/api/health` | Per-topic observed rate and last-seen |
| POST | `/api/sessions/start` | Begin recording (bookmarks a time range) |
| POST | `/api/sessions/{id}/stop` | End a recording |
| GET | `/api/sessions` | List recorded sessions |
| GET | `/api/sessions/{id}/data` | Replay payload (pose trails, series, alerts) |
| WS | `/ws/live` | Live telemetry (stream tail) + alerts (pub/sub) |

## Development & quality

Lint and the full test suite run with no containers — the rule engine, schema, and
simulator logic are pure and infra-free, which is what keeps CI fast:

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -q                  # 13 tests
```

CI runs both as separate jobs on every push. See [`CONTRIBUTING.md`](CONTRIBUTING.md)
and [`CHANGELOG.md`](CHANGELOG.md).

## Engineering decisions

A few choices that make this more than a toy, and what they buy:

- **Stream buffer, not direct DB writes.** Redis Streams decouple producers from storage and survive a worker restart via consumer groups, so no samples are lost during a redeploy.
- **Batched `COPY` ingestion.** The ingest worker accumulates samples and writes them with `copy_records_to_table`, which is dramatically cheaper than row-by-row inserts at sensor rates.
- **Continuous aggregate for history.** Charts over long windows read a 1-second rollup instead of raw rows, keeping payloads small and queries fast; raw data has a 7-day retention policy.
- **Staleness as a first-class signal.** "No data" is often the most important alert in robotics. The engine tracks last-seen time per topic and fires when a stream goes quiet — not just on bad values.
- **Interchangeable producers.** A shared envelope means the synthetic publisher and the ROS 2 bridge are drop-in replacements, which is what lets the project demo with zero hardware.

## Project layout

```
common/   shared telemetry envelope + logging helper (used by every service)
sim/      synthetic fleet publisher  (default data source)
bridge/   ROS 2 rclpy bridge + demo bot  (profile: ros)
ingest/   Redis stream -> TimescaleDB worker
alerts/   threshold + staleness rule engine
api/      FastAPI: REST, /ws/live, static dashboard
api/static/  the dashboard (Three.js + µPlot)
db/       TimescaleDB schema + continuous aggregate
tests/    unit tests: rule engine, schema, simulator
```

## Roadmap

- React + TypeScript dashboard (current frontend is dependency-light vanilla ES)
- rosbag2 export of recorded sessions (session record/replay is already implemented over the telemetry store)
- Zenoh transport option (`zenoh-bridge-ros2dds`) as an alternative to the bridge node
- Statistical / ML anomaly detection on multivariate sensor windows
- Deployed public demo

## License

MIT — see [LICENSE](LICENSE).
