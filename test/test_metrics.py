"""§J — Prometheus metrics registry, /metrics endpoint, JSON-console log toggle."""

from __future__ import annotations

import logging

from utils.metrics import Counter, Histogram, Registry, record_llm_call, render_metrics

# ── registry primitives ──────────────────────────────────────────────────────

def test_counter_renders_prometheus_text():
    reg = Registry()
    c = reg.register(Counter("reqs_total", "requests", labelnames=("route",)))
    c.inc(route="/query")
    c.inc(2.0, route="/query")
    c.inc(route="/factors")
    text = reg.render()
    assert "# TYPE reqs_total counter" in text
    assert 'reqs_total{route="/query"} 3' in text
    assert 'reqs_total{route="/factors"} 1' in text


def test_unlabeled_counter_shows_zero_baseline():
    reg = Registry()
    reg.register(Counter("widgets_total", "widgets"))
    assert "widgets_total 0" in reg.render()


def test_histogram_buckets_are_cumulative():
    reg = Registry()
    h = reg.register(Histogram("lat_seconds", "latency", labelnames=("route",),
                               buckets=(0.1, 0.5, 1.0)))
    for v in (0.05, 0.2, 0.7, 3.0):
        h.observe(v, route="/q")
    text = reg.render()
    assert "# TYPE lat_seconds histogram" in text
    assert 'lat_seconds_bucket{route="/q",le="0.1"} 1' in text     # 0.05
    assert 'lat_seconds_bucket{route="/q",le="0.5"} 2' in text     # 0.05, 0.2
    assert 'lat_seconds_bucket{route="/q",le="1.0"} 3' in text     # +0.7
    assert 'lat_seconds_bucket{route="/q",le="+Inf"} 4' in text    # +3.0
    assert 'lat_seconds_count{route="/q"} 4' in text
    assert 'lat_seconds_sum{route="/q"} ' in text


def test_record_llm_call_updates_default_registry():
    # writes to the shared default REGISTRY, which other suite tests also touch —
    # assert the labelled series exist, not exact counts.
    record_llm_call("openai", "gpt-5.4-nano", "generate",
                    prompt_tokens=100, completion_tokens=20, cost_usd=0.0003)
    text = render_metrics()
    assert 'macrorag_llm_calls_total{provider="openai",model="gpt-5.4-nano",purpose="generate"}' in text
    assert 'macrorag_llm_tokens_total{provider="openai",model="gpt-5.4-nano",kind="prompt"}' in text
    assert "macrorag_llm_cost_usd_total" in text


def test_label_values_are_escaped():
    reg = Registry()
    c = reg.register(Counter("m", "m", labelnames=("path",)))
    c.inc(path='a"b\\c')
    assert 'path="a\\"b\\\\c"' in reg.render()


# ── /metrics endpoint + real-latency middleware ──────────────────────────────

import pytest  # noqa: E402

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from utils.config import RagSettings  # noqa: E402


def test_metrics_endpoint_records_real_requests():
    app = create_app(lambda: object(), RagSettings())   # pipeline never built here
    c = TestClient(app)
    c.get("/healthz")                                    # generate a request to measure
    r = c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    assert "macrorag_http_request_duration_seconds" in body
    assert 'macrorag_http_requests_total{method="GET",route="/healthz",status="200"}' in body


def test_metrics_can_be_disabled():
    s = RagSettings()
    s.observability.metrics_enabled = False
    c = TestClient(create_app(lambda: object(), s))
    assert c.get("/metrics").status_code == 404


# ── logging JSON toggle ──────────────────────────────────────────────────────

def test_json_console_toggle(monkeypatch):
    from utils.logging import JsonFormatter, setup_logging
    monkeypatch.setenv("MACRORAG_LOG_JSON", "1")
    setup_logging()
    stream_handlers = [h for h in logging.getLogger().handlers
                       if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.FileHandler)]
    assert stream_handlers, "expected a console StreamHandler"
    assert all(isinstance(h.formatter, JsonFormatter) for h in stream_handlers)
    # restore human-readable console for the rest of the session
    monkeypatch.delenv("MACRORAG_LOG_JSON", raising=False)
    setup_logging()
