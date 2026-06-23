"""Tests for the benchmark stats helpers (bench/stats.py). Pure, no I/O."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from bench.stats import percentile, summarize


def test_percentile_basic():
    data = list(range(1, 101))           # 1..100
    assert percentile(data, 50) == 50.5
    assert percentile(data, 0) == 1
    assert percentile(data, 100) == 100


def test_percentile_empty_and_single():
    assert percentile([], 95) == 0.0
    assert percentile([42.0], 99) == 42.0


def test_percentile_p99_is_near_top():
    data = [float(i) for i in range(1000)]
    p99 = percentile(data, 99)
    assert 988 <= p99 <= 991        # ~989


def test_summarize_throughput_and_latency():
    lat = [10.0, 20.0, 30.0, 40.0, 50.0]
    out = summarize(lat, count=1000, duration_s=2.0)
    assert out["count"] == 1000
    assert out["throughput_per_s"] == 500.0
    assert out["latency_ms"]["mean"] == 30.0
    assert out["latency_ms"]["max"] == 50.0
    assert out["latency_ms"]["p50"] == 30.0


def test_summarize_handles_empty_latencies():
    out = summarize([], count=0, duration_s=0.0)
    assert out["throughput_per_s"] == 0.0
    assert out["latency_ms"]["mean"] == 0.0
    assert out["latency_ms"]["p99"] == 0.0
