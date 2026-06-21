"""Tests for the shared telemetry envelope (common/schema.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from common.schema import Sample, pose, scalar


def test_scalar_helper_builds_scalar_sample():
    s = scalar("alpha", "/battery_state", {"voltage": 24.1}, ts=10.0)
    assert s.kind == "scalar"
    assert s.metrics == {"voltage": 24.1}
    assert s.pose == {}
    assert s.ts == 10.0


def test_pose_helper_builds_pose_sample():
    s = pose("alpha", "/odom", {"x": 1, "y": 2, "z": 0, "qx": 0, "qy": 0, "qz": 0, "qw": 1})
    assert s.kind == "pose"
    assert s.pose["x"] == 1.0
    assert s.metrics == {}


def test_json_roundtrip_preserves_fields():
    s = scalar("bravo", "/imu", {"accel_z": 9.81, "yaw_rate": -0.2}, ts=42.5)
    back = Sample.from_json(s.to_json())
    assert back.robot_id == "bravo"
    assert back.topic == "/imu"
    assert back.kind == "scalar"
    assert back.ts == 42.5
    assert back.metrics == {"accel_z": 9.81, "yaw_rate": -0.2}


def test_from_json_coerces_values_to_float():
    raw = '{"robot_id":"c","topic":"/t","kind":"scalar","ts":1,"metrics":{"v":3}}'
    s = Sample.from_json(raw)
    assert isinstance(s.metrics["v"], float)
    assert isinstance(s.ts, float)
