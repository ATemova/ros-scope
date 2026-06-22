"""Multivariate anomaly detection for fleet telemetry.

Per-signal thresholds catch "battery below 22 V", but they miss *unusual
combinations* — a CPU-temperature blip that never crosses the hard limit, or a
signal drifting out of its normal relationship with the others. This detector
flags those.

Method: per robot, keep the latest value of each tracked feature; each time all
features are present, push the combined vector into a rolling window. After a
warm-up, score each new vector by its Mahalanobis distance from the window's
mean and covariance (the standard multivariate "how many std-devs away, taking
correlations into account" measure) and flag points beyond a threshold.

The class is pure (no Redis/Postgres), so it is unit-tested directly in
tests/test_anomaly.py.
"""
from __future__ import annotations

from collections import deque

import numpy as np


class AnomalyDetector:
    def __init__(self, features, window=240, warmup=60, threshold=4.0, cooldown_s=20.0):
        self.features = list(features)
        self.window = window
        self.warmup = warmup
        self.threshold = threshold          # Mahalanobis-distance cut
        self.cooldown_s = cooldown_s
        self._latest: dict[str, dict[str, float]] = {}
        self._hist: dict[str, deque] = {}
        self._last_alert: dict[str, float] = {}

    def update(self, robot_id: str, metric: str, value: float, now: float):
        """Feed one metric reading. Returns an anomaly dict when the current
        combined vector is flagged (respecting cooldown), else None."""
        if metric not in self.features:
            return None
        latest = self._latest.setdefault(robot_id, {})
        latest[metric] = value
        if len(latest) < len(self.features):
            return None                     # haven't seen every feature yet

        vec = np.array([latest[f] for f in self.features], dtype=float)
        hist = self._hist.setdefault(robot_id, deque(maxlen=self.window))

        score = None
        if len(hist) >= self.warmup:
            score = _mahalanobis(np.array(hist), vec)
        hist.append(vec)                    # append after scoring (don't score vs itself)

        if score is not None and score > self.threshold:
            if now - self._last_alert.get(robot_id, -1e9) >= self.cooldown_s:
                self._last_alert[robot_id] = now
                return {"robot_id": robot_id, "score": round(float(score), 2),
                        "features": {f: round(float(v), 3) for f, v in zip(self.features, vec, strict=False)}}
        return None


def _mahalanobis(hist: np.ndarray, vec: np.ndarray) -> float:
    mean = hist.mean(axis=0)
    cov = np.atleast_2d(np.cov(hist, rowvar=False))
    cov = cov + np.eye(len(vec)) * 1e-6     # regularize so it stays invertible
    try:
        inv = np.linalg.inv(cov)
    except np.linalg.LinAlgError:
        return 0.0
    d = vec - mean
    return float(np.sqrt(max(0.0, d @ inv @ d)))
