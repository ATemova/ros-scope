"""Pure statistics helpers for the benchmark harness.

Kept free of I/O so the percentile/summary logic is unit-tested directly in
tests/test_bench.py.
"""
from __future__ import annotations


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolation percentile (p in 0..100)."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(latencies_ms: list[float], count: int, duration_s: float) -> dict:
    """Build a benchmark result summary from raw latency samples."""
    thr = round(count / duration_s, 1) if duration_s > 0 else 0.0
    lat = {
        "mean": round(sum(latencies_ms) / len(latencies_ms), 2) if latencies_ms else 0.0,
        "p50": round(percentile(latencies_ms, 50), 2),
        "p95": round(percentile(latencies_ms, 95), 2),
        "p99": round(percentile(latencies_ms, 99), 2),
        "max": round(max(latencies_ms), 2) if latencies_ms else 0.0,
    }
    return {"count": count, "duration_s": round(duration_s, 3),
            "throughput_per_s": thr, "latency_ms": lat}
