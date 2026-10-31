"""C5: finish reasons are normalized and recorded; refusals raise after being billed."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# spec Requirement "Finish reasons are normalized and recorded".
TABLE = {("anthropic", "end_turn"): "stop", ("anthropic", "stop_sequence"): "stop",
         ("anthropic", "max_tokens"): "length", ("anthropic", "refusal"): "refusal",
         ("openai", "stop"): "stop", ("openai", "length"): "length",
         ("openai", "content_filter"): "refusal"}
MAPPED = {raw for _, raw in TABLE}
CALL_KEYS = ("finish_reason", "reasoning_tokens", "served_model")


@pytest.mark.criterion("C5")
def test_normalize_table(lob):
    # oracle: spec Requirement table, plus pass-through of an unmapped value and None.
    from llm.base import normalize_finish_reason

    for (provider, raw), want in TABLE.items():
        assert normalize_finish_reason(provider, raw) == want, (provider, raw)
    assert normalize_finish_reason("anthropic", "pause_turn") == "pause_turn"
    assert normalize_finish_reason("openai", "tool_calls") == "tool_calls"
    assert normalize_finish_reason("openai", None) is None
    assert normalize_finish_reason("anthropic", None) is None


@pytest.mark.criterion("C5")
@settings(max_examples=80, deadline=None)
@given(provider=st.sampled_from(["openai", "anthropic"]),
       raw=st.text(min_size=1, max_size=20).filter(lambda s: s not in MAPPED))
def test_property_unmapped_values_pass_through(lob, provider, raw):
    # oracle: spec Requirement: "any other value passes through unchanged"; the mapped rows
    # of the same provider are checked alongside so a constant cannot pass.
    from llm.base import normalize_finish_reason

    assert normalize_finish_reason(provider, raw) == raw
    for (prov, mapped), want in TABLE.items():
        if prov == provider:
            assert normalize_finish_reason(provider, mapped) == want


def _openai_call(lob, reply: dict, *, model="gpt-5.4"):
    """complete() through the fake OpenAI SDK inside track_usage; returns
    (text or the raised exception, client, aggregate)."""
    from llm.openai_client import OpenAIClient
    from llm.usage import current_usage, track_usage

    with lob.install_fake("openai") as fake:
        fake.REPLY.update(reply)
        client = OpenAIClient(lob.llm_settings("openai"))
        with track_usage("t"):
            try:
                out = client.complete(system="sys", user="q", model=model, purpose="test")
            except Exception as exc:
                out = exc
            agg = current_usage()
    return out, client, agg


def _anthropic_call(lob, reply: dict, *, model="claude-sonnet-4-6"):
    from llm.anthropic_client import AnthropicClient
    from llm.usage import current_usage, track_usage

    with lob.install_fake("anthropic") as fake:
        fake.REPLY.update(reply)
        client = AnthropicClient(lob.llm_settings("anthropic"))
        with track_usage("t"):
            try:
                out = client.complete(system="sys", user="q", model=model, purpose="test")
            except Exception as exc:
                out = exc
            agg = current_usage()
    return out, client, agg


@pytest.mark.criterion("C5")
def test_openai_length_reply_is_recorded_and_returned(lob):
    # oracle: spec Requirement: OpenAI `length` -> `length`; reasoning tokens from
    # usage.completion_tokens_details.reasoning_tokens; served_model = the response's model;
    # last_finish_reason set; a `length` reply returns its partial text.
    out, client, agg = _openai_call(lob, {
        "text": '{"summary": "Yields fe', "finish_reason": "length",
        "completion_tokens": 4000, "reasoning_tokens": 3100,
        "model": "gpt-5.4-2026-03-05"})
    assert out == '{"summary": "Yields fe'
    assert getattr(client, "last_finish_reason", "<unset>") == "length"
    assert agg.calls == 1
    rec = agg.call_log[0]
    assert (rec.finish_reason, rec.reasoning_tokens, rec.served_model) == (
        "length", 3100, "gpt-5.4-2026-03-05")
    d = rec.as_dict()
    assert all(k in d for k in CALL_KEYS), sorted(d)
    assert (d["finish_reason"], d["reasoning_tokens"], d["served_model"]) == (
        "length", 3100, "gpt-5.4-2026-03-05")


@pytest.mark.criterion("C5")
def test_anthropic_max_tokens_reply_is_recorded_and_returned(lob):
    # oracle: spec Requirement: Anthropic `max_tokens` -> `length`; Anthropic reports no
    # reasoning tokens, so 0; served_model = the response's model.
    out, client, agg = _anthropic_call(lob, {
        "text": "partial te", "stop_reason": "max_tokens", "output_tokens": 4000,
        "model": "claude-sonnet-4-6-20260101"})
    assert out == "partial te"
    assert getattr(client, "last_finish_reason", "<unset>") == "length"
    rec = agg.call_log[0]
    assert (rec.finish_reason, rec.reasoning_tokens, rec.served_model) == (
        "length", 0, "claude-sonnet-4-6-20260101")
    d = rec.as_dict()
    assert all(k in d for k in CALL_KEYS), sorted(d)
    assert (d["finish_reason"], d["reasoning_tokens"], d["served_model"]) == (
        "length", 0, "claude-sonnet-4-6-20260101")


@pytest.mark.criterion("C5")
def test_openai_content_filter_raises_after_billing(lob):
    # oracle: spec Requirement "Refusals fail soft": the call is recorded with its cost, then
    # LLMRefusalError(model, category=None) is raised. Hand calculation, configs/
    # llm_pricing.yaml gpt-5.4 (input 2.50, output 15.00 USD per 1M):
    #   (1000*2.50 + 200*15.00)/1e6 = (2500 + 3000)/1e6 = 0.0055
    from llm.base import LLMRefusalError

    out, client, agg = _openai_call(lob, {
        "text": "", "finish_reason": "content_filter", "prompt_tokens": 1000,
        "completion_tokens": 200})
    assert isinstance(out, LLMRefusalError), repr(out)
    assert (out.model, out.category) == ("gpt-5.4", None)
    assert isinstance(out, RuntimeError)
    assert getattr(client, "last_finish_reason", "<unset>") == "refusal"
    assert agg.calls == 1
    assert agg.call_log[0].finish_reason == "refusal"
    # tolerance: abs=1e-12 USD, summing two products of exact decimals
    assert agg.cost_usd == pytest.approx(0.0055, abs=1e-12)


@pytest.mark.criterion("C5")
def test_anthropic_refusal_raises_with_category_after_billing(lob):
    # oracle: spec Requirement: category = Anthropic stop_details.category. Hand calculation,
    # configs/llm_pricing.yaml claude-sonnet-4-6 (input 3.00, output 15.00 USD per 1M):
    #   (1000*3.00 + 50*15.00)/1e6 = (3000 + 750)/1e6 = 0.00375
    from llm.base import LLMRefusalError

    out, client, agg = _anthropic_call(lob, {
        "text": "", "stop_reason": "refusal", "category": "cyber", "input_tokens": 1000,
        "output_tokens": 50})
    assert isinstance(out, LLMRefusalError), repr(out)
    assert (out.model, out.category) == ("claude-sonnet-4-6", "cyber")
    assert getattr(client, "last_finish_reason", "<unset>") == "refusal"
    assert agg.calls == 1
    assert agg.call_log[0].finish_reason == "refusal"
    # tolerance: abs=1e-12 USD, summing two products of exact decimals
    assert agg.cost_usd == pytest.approx(0.00375, abs=1e-12)


@pytest.mark.criterion("C5")
@settings(max_examples=40, deadline=None)
@given(provider=st.sampled_from(["openai", "anthropic"]), pick=st.integers(0, 3),
       reasoning=st.integers(0, 5000), suffix=st.from_regex(r"[0-9]{8}", fullmatch=True))
def test_property_client_records_the_normalized_reason(lob, provider, pick, reasoning,
                                                       suffix):
    # oracle: spec Requirement, computed in the test from TABLE (pass-through otherwise);
    # reasoning tokens as reported (OpenAI) or 0 (Anthropic); served_model as returned.
    # Each provider sees only its own mapped reasons plus one of its unmapped ones.
    raws = {"openai": ("stop", "length", "content_filter", "tool_calls"),
            "anthropic": ("end_turn", "stop_sequence", "max_tokens", "pause_turn")}
    raw = raws[provider][pick]
    model = "gpt-5.4" if provider == "openai" else "claude-sonnet-4-6"
    served = f"{model}-{suffix}"
    if provider == "openai":
        out, client, agg = _openai_call(lob, {"finish_reason": raw, "model": served,
                                              "reasoning_tokens": reasoning})
        want_reasoning = reasoning
    else:
        out, client, agg = _anthropic_call(lob, {"stop_reason": raw, "model": served})
        want_reasoning = 0
    want = TABLE.get((provider, raw), raw)
    if want == "refusal":
        from llm.base import LLMRefusalError
        assert isinstance(out, LLMRefusalError), repr(out)
    else:
        assert out == "fake answer"
    assert getattr(client, "last_finish_reason", "<unset>") == want
    rec = agg.call_log[0]
    assert (rec.finish_reason, rec.reasoning_tokens, rec.served_model) == (
        want, want_reasoning, served)


def _warnings_for(lob, served, model="gpt-5.4"):
    from llm.usage import record_call

    with lob.capture_warnings() as records:
        record_call(provider="openai", model=model, prompt_tokens=10, completion_tokens=5,
                    purpose="test", served_model=served)
    return lob.warnings_in(records)


@pytest.mark.criterion("C5")
def test_record_call_warns_on_a_different_served_model(lob):
    # oracle: spec Requirement: a WARNING when served_model is given and is neither the
    # requested model nor a dated snapshot of it (requested + "-" + a suffix starting
    # with a digit). gpt-5.4-mini is another model, not a snapshot of gpt-5.4.
    assert _warnings_for(lob, "gpt-6-sol") >= 1
    assert _warnings_for(lob, "gpt-5.4x") >= 1
    assert _warnings_for(lob, "gpt-5.4-mini") >= 1
    assert _warnings_for(lob, None) == 0
    assert _warnings_for(lob, "gpt-5.4") == 0
    assert _warnings_for(lob, "gpt-5.4-2026-03-05") == 0


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@settings(max_examples=40, deadline=None)
@given(model=st.sampled_from(["gpt-5.4", "gpt-5.4-mini", "claude-sonnet-4-6",
                              "claude-opus-5-5"]),
       suffix=st.from_regex(r"[0-9][a-z0-9-]{0,9}", fullmatch=True),
       word=st.from_regex(r"[a-z][a-z0-9]{0,9}", fullmatch=True),
       other=st.sampled_from(["gpt-6-luna", "claude-haiku-4-5", "zz-other"]))
def test_snapshot_suffix_is_quiet_and_another_model_warns(lob, model, suffix, word, other):
    # oracle: spec Requirement. Relation: the same call with served_model = model + "-" +
    # a digit-led suffix (a dated snapshot) logs no warning; model + "-" + a letter-led
    # suffix (another model, e.g. -mini) and an unrelated id each log at least one.
    assert _warnings_for(lob, f"{model}-{suffix}", model=model) == 0
    assert _warnings_for(lob, f"{model}-{word}", model=model) >= 1
    assert _warnings_for(lob, other, model=model) >= 1
