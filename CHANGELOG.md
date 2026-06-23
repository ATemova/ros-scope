# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
### Added
- Monitoring stack behind a `monitoring` compose profile: Prometheus scraping
  `/metrics` and a provisioned Grafana dashboard (`monitoring/`).
- Benchmark harness (`bench/`) measuring publish/ingest throughput and end-to-end
  latency, behind a `bench` compose profile; pure stats helpers are unit-tested.
- Prometheus `/metrics` endpoint (ingest rate, active alerts, anomalies, fleet
  KPIs) so rosscope is itself scrapeable and Grafana-graphable. Unit-tested.
- SVG dashboard preview (`docs/preview.svg`) as the README hero until a screen
  recording is added.
- Multivariate anomaly detection: a rolling Mahalanobis-distance detector
  (`alerts/anomaly.py`) flags unusual combinations of signals that fixed
  thresholds miss; configurable under `anomaly:` in `rules.yaml`. Unit-tested.
- Session record & replay: bookmark a window, then scrub it on a timeline
  (play/pause/seek/speed) with 3D trails, charts, and alerts replayed from
  stored data. New `sessions` table and `/api/sessions/*` endpoints.
- `/api/summary` endpoint and a fleet KPI strip on the dashboard (robots online,
  active alerts, lowest battery).
- Click a robot in the 3D legend to focus its signal charts.
- "Connection lost" overlay with automatic WebSocket reconnect.
- Structured logging across services via `common/log.py` (`LOG_LEVEL` env var).
- Ruff linting and an expanded test suite (schema + simulator) wired into CI.
- `pyproject.toml`, `.dockerignore`, and explicit package markers.

## [0.1.0]
### Added
- Initial release: synthetic fleet publisher, ROS 2 bridge, Redis Stream
  pipeline, TimescaleDB ingestion, threshold + staleness alert engine, FastAPI
  REST + live WebSocket, and a Three.js / µPlot dashboard. One-command demo.