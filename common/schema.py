"""Shared telemetry envelope used across every service.

One envelope flows from a producer (the synthetic publisher or the ROS 2
bridge) into the Redis stream, and from there to storage, alerting, and the
live WebSocket fan-out. Keeping the shape in one place means the bridge and
the simulator are interchangeable: storage never knows or cares which one
produced a sample.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Redis keys shared by all services.
STREAM = "telemetry"          # Redis Stream: durable pipeline (storage + alerts)
ALERTS_CHANNEL = "alerts"     # Redis Pub/Sub: alert fan-out to the API

Kind = Literal["scalar", "pose"]


@dataclass
class Sample:
    """A single telemetry sample from one robot on one topic.

    A sample is either:
      - kind="scalar": one or more named numeric metrics (battery voltage,
        cpu temperature, imu accel ...). Each metric becomes a row in the
        `telemetry` hypertable.
      - kind="pose":   a 3D position + orientation quaternion, stored in the
        `poses` hypertable and rendered live in the 3D viewer.
    """

    robot_id: str
    topic: str
    kind: Kind
    ts: float = field(default_factory=time.time)  # unix seconds (float)
    metrics: dict[str, float] = field(default_factory=dict)
    pose: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @staticmethod
    def from_json(raw: str | bytes) -> Sample:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        d: dict[str, Any] = json.loads(raw)
        return Sample(
            robot_id=d["robot_id"],
            topic=d["topic"],
            kind=d["kind"],
            ts=float(d.get("ts", time.time())),
            metrics={k: float(v) for k, v in d.get("metrics", {}).items()},
            pose={k: float(v) for k, v in d.get("pose", {}).items()},
        )


def scalar(robot_id: str, topic: str, metrics: dict[str, float], ts: float | None = None) -> Sample:
    return Sample(robot_id, topic, "scalar", ts or time.time(), metrics=dict(metrics))


def pose(robot_id: str, topic: str, p: dict[str, float], ts: float | None = None) -> Sample:
    return Sample(robot_id, topic, "pose", ts or time.time(), pose=dict(p))
