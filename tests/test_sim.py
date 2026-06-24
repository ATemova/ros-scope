"""Tests for the synthetic publisher's per-robot generator.

These run without Redis: they exercise RobotSim.step() directly and check the
shape of what it emits and that the battery actually drains toward the alert
threshold, so a demo reliably produces alerts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("RATE_HZ", "10")

from sim.publisher import RobotSim, make_room_map


def test_one_tick_emits_expected_kinds():
    sim = RobotSim("alpha", 0)
    samples = sim.step(sim.t0 + 1.0)
    kinds = [s.kind for s in samples]
    assert "pose" in kinds
    assert kinds.count("scalar") >= 3          # battery, diagnostics, imu (+ scan)
    assert all(s.robot_id == "alpha" for s in samples)


def test_battery_drains_below_warning_line():
    sim = RobotSim("charlie", 2)               # charlie drains fast by design
    start = sim.voltage
    for i in range(400):
        sim.step(sim.t0 + i * 0.1)
    assert sim.voltage < start
    assert sim.voltage < 22.0                  # crosses the battery_low threshold


def test_bravo_scan_drops_out_periodically():
    sim = RobotSim("bravo", 1)
    seen_with_scan, seen_without = False, False
    for i in range(400):                        # ~40s spans the dropout cycle
        topics = {s.topic for s in sim.step(sim.t0 + i * 0.1)}
        if "/scan" in topics:
            seen_with_scan = True
        else:
            seen_without = True
    assert seen_with_scan and seen_without      # both states occur -> staleness fires


def test_emits_full_laser_scan_within_a_few_ticks():
    sim = RobotSim("alpha", 0)                  # alpha never drops scan
    scan = None
    for i in range(6):
        for s in sim.step(sim.t0 + i * 0.1):
            if s.kind == "scan":
                scan = s.scan
    assert scan is not None
    assert len(scan["ranges"]) == 120
    assert all(r >= 0 for r in scan["ranges"])
    assert scan["range_max"] > 0


def test_room_map_is_bordered_and_hollow():
    g = make_room_map()
    w, h, d = g["width"], g["height"], g["data"]
    assert w == h == 80
    assert len(d) == w * h
    assert d[0] == 100 and d[w - 1] == 100          # corners are wall
    assert d[(h // 2) * w + w // 2] == 0            # centre is free space
