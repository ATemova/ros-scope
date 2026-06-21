"""Unit tests for the alert rule engine — pure logic, no Redis or Postgres.

Run with:  python -m pytest -q tests
These cover the parts that are easy to get subtly wrong: threshold direction,
cooldown suppression, and staleness timing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from alerts.engine import Cooldowns, evaluate_sample, evaluate_staleness
from common.schema import scalar

THRESHOLDS = [
    {"name": "battery_low", "metric": "voltage", "op": "lt", "bound": 22.0,
     "severity": "warning", "cooldown_s": 30, "message": "low"},
    {"name": "cpu_overheat", "metric": "cpu_temp", "op": "gt", "bound": 75.0,
     "severity": "warning", "cooldown_s": 20, "message": "hot"},
]
STALENESS = [
    {"name": "scan_stale", "topic": "/scan", "timeout_s": 3.0,
     "severity": "warning", "cooldown_s": 15, "message": "stale"},
]


def test_threshold_lt_fires_below_bound():
    s = scalar("alpha", "/battery_state", {"voltage": 21.0}, ts=100.0)
    alerts = evaluate_sample(s, THRESHOLDS, Cooldowns(), now=100.0)
    assert len(alerts) == 1
    assert alerts[0].rule == "battery_low"
    assert alerts[0].severity == "warning"


def test_threshold_lt_silent_above_bound():
    s = scalar("alpha", "/battery_state", {"voltage": 24.0}, ts=100.0)
    assert evaluate_sample(s, THRESHOLDS, Cooldowns(), now=100.0) == []


def test_threshold_gt_fires_above_bound():
    s = scalar("alpha", "/diagnostics", {"cpu_temp": 82.0}, ts=100.0)
    alerts = evaluate_sample(s, THRESHOLDS, Cooldowns(), now=100.0)
    assert [a.rule for a in alerts] == ["cpu_overheat"]


def test_cooldown_suppresses_repeat():
    cd = Cooldowns()
    s = scalar("alpha", "/battery_state", {"voltage": 21.0}, ts=100.0)
    first = evaluate_sample(s, THRESHOLDS, cd, now=100.0)
    second = evaluate_sample(s, THRESHOLDS, cd, now=110.0)   # within 30s cooldown
    third = evaluate_sample(s, THRESHOLDS, cd, now=140.0)    # cooldown elapsed
    assert len(first) == 1 and second == [] and len(third) == 1


def test_pose_sample_never_triggers_threshold():
    from common.schema import pose
    s = pose("alpha", "/odom", {"x": 0, "y": 0, "z": 0, "qx": 0, "qy": 0, "qz": 0, "qw": 1})
    assert evaluate_sample(s, THRESHOLDS, Cooldowns(), now=100.0) == []


def test_staleness_fires_after_timeout():
    last_seen = {("bravo", "/scan"): 100.0}
    # 2s gap: still fresh
    assert evaluate_staleness(last_seen, STALENESS, Cooldowns(), now=102.0) == []
    # 4s gap: stale
    alerts = evaluate_staleness(last_seen, STALENESS, Cooldowns(), now=104.0)
    assert len(alerts) == 1 and alerts[0].rule == "scan_stale"
    assert alerts[0].robot_id == "bravo"
