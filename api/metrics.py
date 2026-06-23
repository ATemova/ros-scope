"""Prometheus text-format exposition for the /metrics endpoint.

rosscope is an observability platform, so it exposes its own metrics for
Prometheus to scrape and Grafana to graph. The formatter is a pure function
(no FastAPI, no database), so it is unit-tested directly in
tests/test_metrics.py.
"""
from __future__ import annotations


def render_prometheus(metrics) -> str:
    """Render Prometheus text exposition format.

    `metrics` is an iterable of (name, type, help, value) tuples. A value of
    None is emitted as NaN (a valid gauge value meaning "no data").
    """
    lines: list[str] = []
    for name, mtype, help_text, value in metrics:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name} {_fmt(value)}")
    return "\n".join(lines) + "\n"


def _fmt(value) -> str:
    if value is None:
        return "NaN"
    f = float(value)
    return str(int(f)) if f.is_integer() else repr(f)