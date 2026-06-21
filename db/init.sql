-- TimescaleDB schema for rosscope.
-- Runs automatically on first container start (mounted into
-- /docker-entrypoint-initdb.d). Idempotent so it is safe to re-run.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------------
-- Fleet metadata. Updated by the ingest worker as samples arrive, so the
-- dashboard can list robots without scanning the time-series tables.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS robots (
    robot_id   TEXT PRIMARY KEY,
    label      TEXT,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Scalar telemetry: one row per (robot, topic, metric) sample.
-- A "long" layout keeps the schema dataset-agnostic: a new metric needs no
-- migration, it is just a new value in the `metric` column.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry (
    time      TIMESTAMPTZ      NOT NULL,
    robot_id  TEXT             NOT NULL,
    topic     TEXT             NOT NULL,
    metric    TEXT             NOT NULL,
    value     DOUBLE PRECISION NOT NULL
);
SELECT create_hypertable('telemetry', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS telemetry_lookup
    ON telemetry (robot_id, metric, time DESC);

-- ---------------------------------------------------------------------------
-- Pose stream: position + orientation quaternion, rendered in the 3D viewer.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS poses (
    time     TIMESTAMPTZ      NOT NULL,
    robot_id TEXT             NOT NULL,
    x DOUBLE PRECISION NOT NULL, y DOUBLE PRECISION NOT NULL, z DOUBLE PRECISION NOT NULL,
    qx DOUBLE PRECISION NOT NULL, qy DOUBLE PRECISION NOT NULL,
    qz DOUBLE PRECISION NOT NULL, qw DOUBLE PRECISION NOT NULL
);
SELECT create_hypertable('poses', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS poses_lookup ON poses (robot_id, time DESC);

-- ---------------------------------------------------------------------------
-- Alerts raised by the rule engine.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    id        BIGSERIAL PRIMARY KEY,
    time      TIMESTAMPTZ NOT NULL DEFAULT now(),
    robot_id  TEXT NOT NULL,
    topic     TEXT,
    rule      TEXT NOT NULL,
    severity  TEXT NOT NULL,          -- info | warning | critical
    message   TEXT NOT NULL,
    value     DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS alerts_recent ON alerts (time DESC);

-- ---------------------------------------------------------------------------
-- Continuous aggregate: 1-second averages. Long history queries hit this
-- instead of raw rows, so a multi-hour chart stays fast. This is the
-- "we thought about scale" detail worth calling out in the README.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS telemetry_1s
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 second', time) AS bucket,
    robot_id,
    metric,
    avg(value) AS avg_value,
    max(value) AS max_value,
    min(value) AS min_value
FROM telemetry
GROUP BY bucket, robot_id, metric
WITH NO DATA;

SELECT add_continuous_aggregate_policy('telemetry_1s',
    start_offset => INTERVAL '10 minutes',
    end_offset   => INTERVAL '10 seconds',
    schedule_interval => INTERVAL '30 seconds',
    if_not_exists => TRUE);

-- Keep raw telemetry for 7 days; the rollup stays longer and is cheap.
SELECT add_retention_policy('telemetry', INTERVAL '7 days', if_not_exists => TRUE);

-- ---------------------------------------------------------------------------
-- Recorded sessions. "Recording" only bookmarks a time range, since every
-- sample is already persisted; replay reads the range back. The API also
-- creates this table on startup so existing database volumes pick it up.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at   TIMESTAMPTZ
);
