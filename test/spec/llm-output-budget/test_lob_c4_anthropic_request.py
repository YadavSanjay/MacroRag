"""C4: the Anthropic client sends the per-tier max_tokens, output_config effort and timeout."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

MODEL = "claude-sonnet-4-6"        # accepts temperature (anthropic_client._NO_SAMPLING_PREFIXES)


def _call(lob, llm, tier, *, model=MODEL):
    """One complete() through a fresh fake SDK under tier_context(tier) (or no tier when
    tier is the string "outside"); returns the recorded create kwargs."""
    from llm.anthropic_client import AnthropicClient
    from llm.usage import tier_context

    with lob.install_fake("anthropic") as fake:
        client = AnthropicClient(llm)
        if tier == "outside":
            client.complete(system="sys", user="question", model=model, purpose="test")
        else:
            with tier_context(tier):
                client.complete(system="sys", user="question", model=model, purpose="test")
    assert len(fake.CALLS) == 1
    return fake.CALLS[0]


def _today(model=MODEL) -> dict:
    # oracle helper: today's request (llm/anthropic_client.py before this change) with the
    # LLMSettings defaults prompt_cache on, max_tokens 4000 and temperature 0.1.
    return {"model": model,
            "system": [{"type": "text", "text": "sys",
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": "question"}],
            "max_tokens": 4000, "temperature": 0.1}


@pytest.mark.criterion("C4")
def test_tier_with_all_options_sends_them(lob):
    # oracle: spec Requirement "Calls carry their tier" (Anthropic): max_tokens = the tier's
    # max_tokens, output_config={"effort": effort}, timeout = its timeout_s.
    llm = lob.llm_settings("anthropic", options={"anthropic": {
        "complex": {"max_tokens": 12000, "effort": "xhigh", "timeout_s": 90.0}}})
    kw = _call(lob, llm, "complex")
    assert kw["max_tokens"] == 12000
    assert kw.get("output_config") == {"effort": "xhigh"}
    # tolerance: the configured timeout is passed through unchanged
    assert kw.get("timeout") == pytest.approx(90.0, rel=1e-12)
    assert kw["model"] == MODEL


@pytest.mark.criterion("C4")
def test_unset_options_are_omitted_and_outside_is_today(lob):
    # oracle: spec Requirement: output_config and timeout are sent only when set; an unset
    # max_tokens falls back to llm.max_tokens; outside any tier, or for a tier with no
    # options, the request is as today.
    llm = lob.llm_settings("anthropic", options={"anthropic": {
        "answer": {"max_tokens": 800},
        "plan": {"timeout_s": 15.0}}})
    answer = _call(lob, llm, "answer")
    assert answer["max_tokens"] == 800
    assert "output_config" not in answer and "timeout" not in answer
    plan = _call(lob, llm, "plan")
    assert plan["max_tokens"] == 4000
    assert "output_config" not in plan
    # tolerance: the configured timeout is passed through unchanged
    assert plan.get("timeout") == pytest.approx(15.0, rel=1e-12)
    assert _call(lob, llm, "outside") == _today()
    assert _call(lob, llm, "complex") == _today()


@pytest.mark.criterion("C4")
def test_options_come_from_the_active_provider(lob):
    # oracle: spec Requirement "Per-tier options": the active provider (anthropic) supplies
    # the options; the openai profile's options for the same tier do not apply.
    llm = lob.llm_settings("anthropic", options={
        "openai": {"plan": {"max_tokens": 1111, "effort": "none"}},
        "anthropic": {"plan": {"max_tokens": 3333, "effort": "medium"}}})
    kw = _call(lob, llm, "plan")
    assert kw["max_tokens"] == 3333
    assert kw.get("output_config") == {"effort": "medium"}


_OPT = st.fixed_dictionaries({}, optional={
    "max_tokens": st.integers(1, 20_000),
    "effort": st.sampled_from(["low", "medium", "high", "xhigh", "max"]),
    "timeout_s": st.integers(1, 600).map(float)})


@pytest.mark.criterion("C4")
@settings(max_examples=50, deadline=None)
@given(tier=st.sampled_from(["plan", "answer", "complex", "default", "escalation"]),
       opts=_OPT, max_tokens=st.integers(100, 20_000))
def test_property_request_follows_the_tier_options(lob, tier, opts, max_tokens):
    # oracle: spec Requirement (Anthropic bullet), computed in the test from the options the
    # settings were built with; a fixed sentinel tier pins that options are applied at all.
    llm = lob.llm_settings("anthropic", options={"anthropic": {tier: opts}},
                           max_tokens=max_tokens)
    kw = _call(lob, llm, tier)
    assert kw["max_tokens"] == opts.get("max_tokens", max_tokens)
    if "effort" in opts:
        assert kw.get("output_config") == {"effort": opts["effort"]}
    else:
        assert "output_config" not in kw
    if "timeout_s" in opts:
        # tolerance: the configured timeout is passed through unchanged
        assert kw.get("timeout") == pytest.approx(opts["timeout_s"], rel=1e-12)
    else:
        assert "timeout" not in kw
    sentinel = lob.llm_settings("anthropic", options={"anthropic": {
        "default": {"max_tokens": 4321, "effort": "high"}}}, max_tokens=max_tokens)
    skw = _call(lob, sentinel, "default")
    assert (skw["max_tokens"], skw.get("output_config")) == (4321, {"effort": "high"})
