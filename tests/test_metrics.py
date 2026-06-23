"""Tests for the Prometheus exposition formatter (api/metrics.py).

Pure logic, no FastAPI or database needed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from api.metrics import render_prometheus


def test_emits_help_type_and_value():
    out = render_prometheus([("rosscope_robots_online", "gauge", "Robots online", 3)])
    assert "# HELP rosscope_robots_online Robots online" in out
    assert "# TYPE rosscope_robots_online gauge" in out
    assert "rosscope_robots_online 3" in out
    assert out.endswith("\n")


def test_none_becomes_nan():
    out = render_prometheus([("x", "gauge", "h", None)])
    assert "x NaN" in out


def test_integer_valued_float_has_no_decimal():
    out = render_prometheus([("a", "gauge", "h", 4.0), ("b", "gauge", "h", 21.5)])
    assert "\na 4\n" in out
    assert "b 21.5" in out


def test_multiple_metrics_each_have_a_header_block():
    out = render_prometheus([
        ("m1", "gauge", "first", 1),
        ("m2", "gauge", "second", 2),
    ])
    assert out.count("# TYPE") == 2
    assert out.count("# HELP") == 2
