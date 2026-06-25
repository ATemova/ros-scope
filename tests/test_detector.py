"""Tests for the offline-trained anomaly detector (alerts/detector.py)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from alerts.detector import (
    LearnedDetector,
    _batch_mahalanobis,
    fit,
    load_model,
    predict,
    save_model,
    score,
)

FEATS = ["voltage", "cpu_temp", "yaw_rate"]


def _normal(n, rng):
    return np.column_stack([
        rng.uniform(20.0, 25.2, n),
        48 + rng.normal(0, 1.5, n),
        rng.normal(0, 0.05, n),
    ])


def test_threshold_calibrated_to_target_fpr():
    rng = np.random.default_rng(0)
    x = _normal(5000, rng)
    m = fit(x, FEATS, target_fpr=0.01)
    d = _batch_mahalanobis(x, np.array(m["mean"]), np.array(m["inv_cov"]))
    fpr = float((d > m["threshold"]).mean())
    assert 0.005 <= fpr <= 0.02            # ~1% of clean training points flagged


def test_anomaly_scores_higher_than_normal():
    rng = np.random.default_rng(1)
    m = fit(_normal(4000, rng), FEATS)
    normal_vec = np.array([23.0, 48.0, 0.0])
    spike_vec = np.array([23.0, 82.0, 0.0])      # CPU thermal spike
    assert score(m, spike_vec) > score(m, normal_vec)
    assert predict(m, spike_vec)
    assert not predict(m, normal_vec)


def test_save_load_roundtrip(tmp_path):
    rng = np.random.default_rng(2)
    m = fit(_normal(2000, rng), FEATS)
    p = tmp_path / "m.json"
    save_model(m, str(p))
    m2 = load_model(str(p))
    assert m2["features"] == FEATS
    assert abs(m2["threshold"] - m["threshold"]) < 1e-9


def test_learned_detector_flags_spike_after_full_vector():
    rng = np.random.default_rng(3)
    m = fit(_normal(4000, rng), FEATS)
    det = LearnedDetector(m, cooldown_s=0)
    assert det.update("a", "voltage", 23.0, 1.0) is None     # vector incomplete
    assert det.update("a", "cpu_temp", 48.0, 1.0) is None    # vector incomplete
    assert det.update("a", "yaw_rate", 0.0, 1.0) is None     # complete + normal
    hit = det.update("a", "cpu_temp", 85.0, 2.0)             # spike completes vector
    assert hit is not None
    assert hit["robot_id"] == "a"
    assert hit["score"] > m["threshold"]
