"""Tests for the multivariate anomaly detector (alerts/anomaly.py).

Pure logic, only needs numpy. Feeds controlled vectors so the behaviour is
deterministic: a normal cluster must stay quiet, a clear outlier must fire, and
the cooldown must suppress repeats.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from alerts.anomaly import AnomalyDetector

FEATURES = ["a", "b", "c"]


def feed(det, robot, vec, t):
    """Push one full feature vector; return an anomaly if any metric update
    fires (the detector re-scores on each metric, and cooldown may suppress
    later ones within the same vector)."""
    hit = None
    for name, val in zip(FEATURES, vec, strict=False):
        res = det.update(robot, name, val, t)
        if res:
            hit = res
    return hit


def test_no_alert_before_warmup():
    det = AnomalyDetector(FEATURES, window=100, warmup=30, threshold=4.0, cooldown_s=0)
    random.seed(0)
    fired = [feed(det, "r1", [random.gauss(0, 1) for _ in FEATURES], i) for i in range(20)]
    assert not any(fired)


def test_normal_cluster_stays_quiet():
    det = AnomalyDetector(FEATURES, window=300, warmup=50, threshold=4.0, cooldown_s=0)
    random.seed(1)
    fired = 0
    for i in range(300):
        if feed(det, "r1", [random.gauss(0, 1) for _ in FEATURES], i):
            fired += 1
    assert fired <= 3            # a couple of statistical flukes at most


def test_clear_outlier_fires():
    det = AnomalyDetector(FEATURES, window=300, warmup=50, threshold=4.0, cooldown_s=0)
    random.seed(2)
    for i in range(120):         # build a tight baseline around 0
        feed(det, "r1", [random.gauss(0, 1) for _ in FEATURES], i)
    hit = feed(det, "r1", [40.0, -40.0, 40.0], 200)   # far outside the cluster
    assert hit is not None
    assert hit["robot_id"] == "r1"
    assert hit["score"] > 4.0


def test_incomplete_vector_returns_none():
    det = AnomalyDetector(FEATURES, warmup=5)
    assert det.update("r1", "a", 1.0, 0) is None      # only one feature seen


def test_cooldown_suppresses_repeats():
    det = AnomalyDetector(FEATURES, window=300, warmup=50, threshold=4.0, cooldown_s=30)
    random.seed(3)
    for i in range(120):
        feed(det, "r1", [random.gauss(0, 1) for _ in FEATURES], i)
    first = feed(det, "r1", [40.0, -40.0, 40.0], 200)
    second = feed(det, "r1", [42.0, -41.0, 39.0], 205)   # within cooldown
    assert first is not None and second is None
