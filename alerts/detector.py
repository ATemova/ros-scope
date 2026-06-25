"""Offline-trained anomaly detector.

The rolling detector in anomaly.py recomputes mean/covariance from a moving
window at runtime: it adapts, but it has no calibrated threshold and a sustained
fault can slowly poison its own baseline. This module is the trained alternative:

  1. fit a Gaussian model (mean + covariance) on a curated *clean* dataset,
  2. calibrate the decision threshold to a target false-positive rate,
  3. freeze and version the model to JSON,
  4. score live vectors by Mahalanobis distance against the frozen model.

Training, evaluation (precision/recall against injected faults), and the model
file are produced by train_detector.py. Everything here is pure numpy, so it is
unit-tested directly in tests/test_detector.py.
"""
from __future__ import annotations

import json

import numpy as np

MODEL_VERSION = 1


def fit(x: np.ndarray, features: list[str], target_fpr: float = 0.01, reg: float = 1e-6) -> dict:
    """Fit mean + covariance on clean data and calibrate the threshold so that
    only `target_fpr` of the training points are flagged."""
    x = np.asarray(x, dtype=float)
    mean = x.mean(axis=0)
    cov = np.atleast_2d(np.cov(x, rowvar=False)) + np.eye(x.shape[1]) * reg
    inv = np.linalg.inv(cov)
    dists = _batch_mahalanobis(x, mean, inv)
    threshold = float(np.quantile(dists, 1.0 - target_fpr))
    return {
        "version": MODEL_VERSION,
        "features": list(features),
        "mean": mean.tolist(),
        "inv_cov": inv.tolist(),
        "threshold": threshold,
        "target_fpr": target_fpr,
        "trained_on": int(x.shape[0]),
    }


def score(model: dict, vec: np.ndarray) -> float:
    """Mahalanobis distance of one vector from the trained model."""
    mean = np.asarray(model["mean"], dtype=float)
    inv = np.asarray(model["inv_cov"], dtype=float)
    d = np.asarray(vec, dtype=float) - mean
    return float(np.sqrt(max(0.0, d @ inv @ d)))


def predict(model: dict, vec: np.ndarray) -> bool:
    return score(model, vec) > model["threshold"]


def _batch_mahalanobis(x: np.ndarray, mean: np.ndarray, inv: np.ndarray) -> np.ndarray:
    d = x - mean
    return np.sqrt(np.maximum(0.0, np.einsum("ij,jk,ik->i", d, inv, d)))


def save_model(model: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(model, f, indent=2)


def load_model(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


class LearnedDetector:
    """Drop-in replacement for AnomalyDetector that scores against a frozen,
    pre-trained model instead of a rolling window. Same update() interface."""

    def __init__(self, model: dict, cooldown_s: float = 20.0) -> None:
        self.model = model
        self.features = list(model["features"])
        self.cooldown_s = cooldown_s
        self._latest: dict[str, dict[str, float]] = {}
        self._last_alert: dict[str, float] = {}

    def update(self, robot_id: str, metric: str, value: float, now: float):
        if metric not in self.features:
            return None
        latest = self._latest.setdefault(robot_id, {})
        latest[metric] = value
        if len(latest) < len(self.features):
            return None
        vec = np.array([latest[f] for f in self.features], dtype=float)
        sc = score(self.model, vec)
        if sc > self.model["threshold"]:
            if now - self._last_alert.get(robot_id, -1e9) >= self.cooldown_s:
                self._last_alert[robot_id] = now
                return {"robot_id": robot_id, "score": round(sc, 2),
                        "features": {f: round(float(v), 3)
                                     for f, v in zip(self.features, vec, strict=False)}}
        return None
