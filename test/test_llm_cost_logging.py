"""LLM cost/usage logging: pricing math, per-call records, and the re-entrant
per-query aggregation the pipeline wraps its entry points in."""

from __future__ import annotations

import logging

import pytest

from llm import pricing
from llm.pricing import cost_usd, model_price
from llm.usage import (
    configure_call_capture,
    current_usage,
    record_call,
    track_usage,
    usage_snapshot,
)

# ── pricing ──────────────────────────────────────────────────────────────────

def test_cost_uses_input_output_and_cache_rates():
    # 1M input + 1M output, none cached -> input rate + output rate (per 1M).
    # Derived from the shipped table so the test survives price edits.
    price = model_price("gpt-5.4-mini")
    cost = cost_usd("gpt-5.4-mini", prompt_tokens=1_000_000,
                    completion_tokens=1_000_000)
    assert cost == pytest.approx(price["input"] + price["output"])


def test_cached_tokens_billed_at_cache_rate():
    # 1M prompt all cached -> billed entirely at cache_read, not the input rate.
    price = model_price("gpt-5.4-mini")
    cost = cost_usd("gpt-5.4-mini", prompt_tokens=1_000_000,
                    completion_tokens=0, cached_tokens=1_000_000)
    assert cost == pytest.approx(price["cache_read"])
    assert price["cache_read"] < price["input"]   # cache really is cheaper


def test_prefix_fallback_resolves_dated_model_ids():
    assert model_price("gpt-5.4-mini-2026-01-01") == model_price("gpt-5.4-mini")


def test_unknown_model_returns_none_and_warns_once(caplog):
    pricing._warned.discard("totally-made-up-model")
    with caplog.at_level(logging.WARNING):
        assert cost_usd("totally-made-up-model", prompt_tokens=10) is None
        cost_usd("totally-made-up-model", prompt_tokens=10)  # second call: no new warn
    warnings = [r for r in caplog.records if "no pricing" in r.message]
    assert len(warnings) == 1


# ── per-call + aggregation ───────────────────────────────────────────────────

def test_record_call_logs_a_line(caplog):
    with caplog.at_level(logging.INFO, logger="llm.calls"):
        record_call(provider="openai", model="gpt-5.4-nano", prompt_tokens=100,
                    completion_tokens=50, latency_s=0.3, purpose="generate")
    line = next(r.message for r in caplog.records if r.message.startswith("LLM call"))
    assert "model=gpt-5.4-nano" in line and "purpose=generate" in line
    assert "prompt=100" in line and "completion=50" in line and "cost=$" in line


def test_track_usage_aggregates_nested_calls(caplog):
    with caplog.at_level(logging.INFO, logger="llm.calls"):
        with track_usage("query") as agg:
            record_call(provider="openai", model="gpt-5.4-mini",
                        prompt_tokens=1000, completion_tokens=200, purpose="plan")
            record_call(provider="openai", model="gpt-5.4",
                        prompt_tokens=2000, completion_tokens=500, purpose="generate")
            assert agg.calls == 2
            assert agg.prompt_tokens == 3000 and agg.completion_tokens == 700
            assert agg.cost_complete and agg.cost_usd > 0
    summary = next(r.message for r in caplog.records
                   if r.message.startswith("LLM usage [query]"))
    assert "calls=2" in summary and "total_tokens=3700" in summary and "cost=$" in summary


def test_track_usage_is_reentrant_single_summary(caplog):
    """A nested track_usage folds into the outer one — only one summary is logged
    (mirrors pipeline.synthesize -> pipeline.answer)."""
    with caplog.at_level(logging.INFO, logger="llm.calls"):
        with track_usage("synthesize"):
            record_call(provider="openai", model="gpt-5.4-nano", prompt_tokens=10,
                        completion_tokens=5, purpose="synthesize")
            with track_usage("query"):  # re-entrant: joins the outer aggregate
                record_call(provider="openai", model="gpt-5.4-nano",
                            prompt_tokens=20, completion_tokens=5, purpose="generate")
    summaries = [r.message for r in caplog.records if r.message.startswith("LLM usage")]
    assert len(summaries) == 1
    assert summaries[0].startswith("LLM usage [synthesize]") and "calls=2" in summaries[0]


def test_unpriced_model_marks_aggregate_incomplete(caplog):
    pricing._warned.discard("mystery-model")
    with caplog.at_level(logging.INFO, logger="llm.calls"):
        with track_usage("query") as agg:
            record_call(provider="openai", model="mystery-model",
                        prompt_tokens=100, completion_tokens=10)
    assert agg.cost_complete is False
    summary = next(r.message for r in caplog.records if r.message.startswith("LLM usage"))
    assert "unpriced" in summary


def test_record_call_outside_context_is_a_noop_aggregate():
    assert current_usage() is None
    # must not raise without an active track_usage
    record_call(provider="openai", model="gpt-5.4-nano", prompt_tokens=1,
                completion_tokens=1)
    assert current_usage() is None


# ── prompt/response capture + snapshot ───────────────────────────────────────

def test_usage_snapshot_captures_per_call_prompt_and_response():
    configure_call_capture(enabled=True, max_chars=20_000)
    with track_usage("query"):
        record_call(provider="openai", model="gpt-5.4-mini", prompt_tokens=100,
                    completion_tokens=20, cached_tokens=10, purpose="plan",
                    system="sys", user="hello", response="world")
        snap = usage_snapshot()
    assert snap["calls"] == 1
    assert snap["total_tokens"] == 120 and snap["cached_tokens"] == 10
    call = snap["call_log"][0]
    assert call["purpose"] == "plan" and call["cost_usd"] is not None
    assert call["system"] == "sys" and call["user"] == "hello"
    assert call["response"] == "world"


def test_capture_disabled_keeps_tokens_but_drops_prompt_text():
    configure_call_capture(enabled=False, max_chars=20_000)
    try:
        with track_usage("query"):
            record_call(provider="openai", model="gpt-5.4-mini", prompt_tokens=5,
                        completion_tokens=5, purpose="plan",
                        system="sys", user="u", response="r")
            snap = usage_snapshot()
    finally:
        configure_call_capture(enabled=True, max_chars=20_000)   # restore default
    call = snap["call_log"][0]
    assert call["prompt_tokens"] == 5                     # accounting kept
    assert call["system"] is None and call["response"] is None  # bodies dropped


def test_capture_truncates_oversized_bodies():
    configure_call_capture(enabled=True, max_chars=10)
    try:
        with track_usage("query"):
            record_call(provider="openai", model="gpt-5.4-mini", purpose="generate",
                        user="x" * 500, response="y" * 500)
            snap = usage_snapshot()
    finally:
        configure_call_capture(enabled=True, max_chars=20_000)
    call = snap["call_log"][0]
    assert call["user"].startswith("xxxxxxxxxx") and "truncated" in call["user"]


def test_usage_snapshot_none_outside_context():
    assert usage_snapshot() is None
