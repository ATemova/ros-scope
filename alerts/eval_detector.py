"""Head-to-head evaluation: learned (frozen) vs rolling (online) detector.

Both detectors are run over the *same* labeled telemetry stream so their
precision/recall are directly comparable. The stream is a warm-up block of clean
data followed by a test block that interleaves normal samples with injected
faults (thermal spikes, out-of-envelope yaw) at known positions.

    python3 -m alerts.eval_detector
    python3 -m alerts.eval_detector --anomaly-rate 0.15 --test 3000

This is the evidence behind the README's claim that the trained model beats the
rolling fallback: the rolling detector adapts, but interspersed faults slowly
leak into its moving baseline, costing recall.
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from alerts.anomaly import AnomalyDetector
from alerts.detector import load_model, predict
from alerts.train_detector import FEATURES, sample_anomalies, sample_normal


def _scores(tp: int, fp: int, fn: int, n_norm: int) -> dict:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "false_positive_rate": round(fp / n_norm, 4) if n_norm else 0.0,
            "tp": tp, "fp": fp, "fn": fn}


def build_stream(rng, n_test: int, anomaly_rate: float):
    """Return (vectors, labels) for the test block (1 = injected fault)."""
    labels = (rng.random(n_test) < anomaly_rate).astype(int)
    n_anom = int(labels.sum())
    normals = sample_normal(n_test - n_anom, rng)
    anoms = sample_anomalies(max(1, n_anom), rng)
    vectors = np.zeros((n_test, len(FEATURES)))
    ni = ai = 0
    for i, lab in enumerate(labels):
        if lab:
            vectors[i] = anoms[ai % len(anoms)]
            ai += 1
        else:
            vectors[i] = normals[ni % len(normals)]
            ni += 1
    return vectors, labels


def eval_learned(model: dict, vectors: np.ndarray, labels: np.ndarray) -> dict:
    flags = np.array([predict(model, v) for v in vectors], dtype=int)
    tp = int(((flags == 1) & (labels == 1)).sum())
    fp = int(((flags == 1) & (labels == 0)).sum())
    fn = int(((flags == 0) & (labels == 1)).sum())
    return _scores(tp, fp, fn, int((labels == 0).sum()))


def eval_rolling(vectors: np.ndarray, labels: np.ndarray, warmup_block: np.ndarray) -> dict:
    det = AnomalyDetector(FEATURES, window=240, warmup=60, threshold=4.0, cooldown_s=0.0)
    now = 0.0
    for v in warmup_block:                       # establish a clean baseline first
        for f, val in zip(FEATURES, v, strict=False):
            det.update("eval", f, float(val), now)
        now += 1.0
    tp = fp = fn = 0
    for v, lab in zip(vectors, labels, strict=False):
        hit = None
        for f, val in zip(FEATURES, v, strict=False):
            hit = det.update("eval", f, float(val), now)
        now += 1.0
        flagged = hit is not None
        if flagged and lab:
            tp += 1
        elif flagged and not lab:
            fp += 1
        elif not flagged and lab:
            fn += 1
    return _scores(tp, fp, fn, int((labels == 0).sum()))


def main() -> None:
    ap = argparse.ArgumentParser(description="learned vs rolling anomaly detector")
    ap.add_argument("--model", default="alerts/model.json")
    ap.add_argument("--test", type=int, default=3000)
    ap.add_argument("--anomaly-rate", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    warmup_block = sample_normal(400, rng)
    vectors, labels = build_stream(rng, args.test, args.anomaly_rate)
    model = load_model(args.model)

    result = {
        "test_samples": args.test,
        "injected_faults": int(labels.sum()),
        "learned": eval_learned(model, vectors, labels),
        "rolling": eval_rolling(vectors, labels, warmup_block),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
