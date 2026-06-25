"""Train and evaluate the offline anomaly detector.

Synthesizes a clean telemetry dataset matching the fleet's *normal* behaviour,
fits the model, calibrates the threshold, then evaluates precision/recall against
injected faults (CPU-temperature spikes and out-of-envelope yaw). Writes the
versioned model to alerts/model.json and prints the metrics as JSON.

    python3 -m alerts.train_detector                 # synthetic clean data
    python3 -m alerts.train_detector --out alerts/model.json --fpr 0.01

The detector is unsupervised — it never sees faults during training; the labels
exist only to measure detection quality afterwards.
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from alerts.detector import _batch_mahalanobis, fit, save_model

FEATURES = ["voltage", "cpu_temp", "yaw_rate"]


def sample_normal(n: int, rng: np.random.Generator) -> np.ndarray:
    """Clean operating envelope, consistent with the synthetic publisher."""
    phase = rng.uniform(0, 2 * np.pi, n)
    voltage = rng.uniform(20.0, 25.2, n)                      # pack draining over time
    cpu_temp = 48 + 6 * np.sin(phase) + rng.normal(0, 1.5, n)  # baseline + load wave
    yaw_rate = 0.4 * np.sin(phase) + rng.normal(0, 0.05, n)
    return np.column_stack([voltage, cpu_temp, yaw_rate])


def sample_anomalies(n: int, rng: np.random.Generator) -> np.ndarray:
    """Two fault families: thermal spikes and out-of-envelope yaw."""
    base = sample_normal(n, rng)
    half = n // 2
    base[:half, 1] += rng.uniform(25, 35, half)              # CPU thermal spike
    base[half:, 2] = rng.choice([-1, 1], n - half) * rng.uniform(1.5, 3.0, n - half)  # yaw
    return base


def evaluate(model: dict, rng: np.random.Generator, n_norm: int, n_anom: int) -> dict:
    normal = sample_normal(n_norm, rng)
    anom = sample_anomalies(n_anom, rng)
    mean = np.asarray(model["mean"])
    inv = np.asarray(model["inv_cov"])
    thr = model["threshold"]
    norm_flagged = _batch_mahalanobis(normal, mean, inv) > thr
    anom_flagged = _batch_mahalanobis(anom, mean, inv) > thr
    tp = int(anom_flagged.sum())
    fn = int((~anom_flagged).sum())
    fp = int(norm_flagged.sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "false_positive_rate": round(fp / n_norm, 4),
        "eval": {"normal": n_norm, "anomalies": n_anom, "tp": tp, "fp": fp, "fn": fn},
        "threshold": round(thr, 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="train + eval the anomaly detector")
    ap.add_argument("--out", default="alerts/model.json")
    ap.add_argument("--fpr", type=float, default=0.01, help="target false-positive rate")
    ap.add_argument("--train", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    train = sample_normal(args.train, rng)
    model = fit(train, FEATURES, target_fpr=args.fpr)
    metrics = evaluate(model, rng, n_norm=4000, n_anom=2000)
    model["metrics"] = metrics
    save_model(model, args.out)
    print(json.dumps({"model": args.out, "features": FEATURES,
                      "trained_on": model["trained_on"], **metrics}, indent=2))


if __name__ == "__main__":
    main()
