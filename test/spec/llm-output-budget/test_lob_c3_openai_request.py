"""C3: the OpenAI client sends the per-tier output cap, reasoning effort and timeout."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

MODEL = "gpt-5.4-mini"        # accepts temperature (openai_client.accepts_temperature)


def _call(lob, llm, tier, *, model=MODEL):
    """One complete() through a fresh fake SDK under tier_context(tier) (or no tier when
    tier is the string "outside"); returns the recorded create kwargs."""
    from llm.openai_client import OpenAIClient
    from llm.usage import tier_context

    with lob.install_fake("openai") as fake:
        client = OpenAIClient(llm)
        if tier == "outside":
            client.complete(system="sys", user="question", model=model, purpose="test")
        else:
            with tier_context(tier):
                client.complete(system="sys", user="question", model=model, purpose="test")
    assert len(fake.CALLS) == 1
    return fake.CALLS[0]


def _today(model=MODEL) -> dict:
    # oracle helper: today's request (llm/openai_client.py before this change) with the
    # LLMSettings defaults max_tokens 4000 and temperature 0.1.
    return {"model": model,
            "messages": [{"role": "system", "content": "sys"},
                         {"role": "user", "content": "question"}],
            "max_completion_tokens": 4000, "temperature": 0.1}


@pytest.mark.criterion("C3")
def test_tier_with_all_options_sends_them(lob):
    # oracle: spec Requirement "Calls carry their tier" (OpenAI): max_completion_tokens =
    # the tier's max_tokens, reasoning_effort = its effort, timeout = its timeout_s.
    llm = lob.llm_settings("openai", options={"openai": {
        "plan": {"max_tokens": 1500, "effort": "low", "timeout_s": 20.0}}})
    kw = _call(lob, llm, "plan")
    assert kw["max_completion_tokens"] == 1500
    assert kw.get("reasoning_effort") == "low"
    # tolerance: the configured timeout is passed through unchanged
    assert kw.get("timeout") == pytest.approx(20.0, rel=1e-12)
    assert kw["model"] == MODEL


@pytest.mark.criterion("C3")
def test_unset_options_are_omitted_and_outside_is_today(lob):
    # oracle: spec Requirement: reasoning_effort and timeout are sent only when set; an unset
    # max_tokens falls back to llm.max_tokens; outside any tier, or for a tier with no
    # options, the request is as today.
    llm = lob.llm_settings("openai", options={"openai": {
        "answer": {"max_tokens": 900},
        "complex": {"effort": "high"}}})
    answer = _call(lob, llm, "answer")
    assert answer["max_completion_tokens"] == 900
    assert "reasoning_effort" not in answer and "timeout" not in answer
    cplx = _call(lob, llm, "complex")
    assert cplx.get("reasoning_effort") == "high"
    assert cplx["max_completion_tokens"] == 4000
    assert "timeout" not in cplx
    assert _call(lob, llm, "outside") == _today()
    assert _call(lob, llm, "plan") == _today()


@pytest.mark.criterion("C3")
def test_options_come_from_the_active_replacement_profile(lob):
    # oracle: spec Requirement "Per-tier options": with use_replacement_models on, the
    # openai replacement entry's options apply, not the profiles entry's.
    llm = lob.llm_settings("openai",
                           options={"openai": {"plan": {"max_tokens": 1111}}},
                           replacement_options={"openai": {"plan": {
                               "max_tokens": 2222, "effort": "max"}}},
                           use_replacement=True)
    kw = _call(lob, llm, "plan", model="gpt-6-sol")
    assert kw["max_completion_tokens"] == 2222
    assert kw.get("reasoning_effort") == "max"


_OPT = st.fixed_dictionaries({}, optional={
    "max_tokens": st.integers(1, 64_000),
    "effort": st.sampled_from(["none", "low", "medium", "high", "xhigh", "max"]),
    "timeout_s": st.integers(1, 600).map(float)})


@pytest.mark.criterion("C3")
@settings(max_examples=50, deadline=None)
@given(tier=st.sampled_from(["plan", "answer", "complex", "default", "escalation"]),
       opts=_OPT, max_tokens=st.integers(100, 32_000))
def test_property_request_follows_the_tier_options(lob, tier, opts, max_tokens):
    # oracle: spec Requirement (OpenAI bullet), computed in the test from the options the
    # settings were built with; a fixed sentinel tier pins that options are applied at all.
    llm = lob.llm_settings("openai", options={"openai": {tier: opts}}, max_tokens=max_tokens)
    kw = _call(lob, llm, tier)
    assert kw["max_completion_tokens"] == opts.get("max_tokens", max_tokens)
    if "effort" in opts:
        assert kw.get("reasoning_effort") == opts["effort"]
    else:
        assert "reasoning_effort" not in kw
    if "timeout_s" in opts:
        # tolerance: the configured timeout is passed through unchanged
        assert kw.get("timeout") == pytest.approx(opts["timeout_s"], rel=1e-12)
    else:
        assert "timeout" not in kw
    sentinel = lob.llm_settings("openai", options={"openai": {
        "default": {"max_tokens": 1234, "effort": "low"}}}, max_tokens=max_tokens)
    skw = _call(lob, sentinel, "default")
    assert (skw["max_completion_tokens"], skw.get("reasoning_effort")) == (1234, "low")
