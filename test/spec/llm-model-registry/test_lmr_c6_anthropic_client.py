"""C6: the Anthropic client prices cache reads and cache writes into the usage aggregate."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HC = [HealthCheck.function_scoped_fixture]

# Copied from the spec Notes table: USD per 1M tokens, (input, cache_read, cache_write, output).
ANTHROPIC = {
    "claude-opus-5-5": (4.00, 0.20, 5.00, 20.00),
    "claude-sonnet-5-5": (2.00, 0.20, 2.50, 10.00),
    "claude-haiku-5-5": (0.10, 0.01, 0.125, 0.50),
    "claude-opus-4-8": (5.00, 0.50, 6.25, 25.00),
    "claude-opus-4-7": (5.00, 0.50, 6.25, 25.00),
    "claude-sonnet-4-6": (3.00, 0.30, 3.75, 15.00),
    "claude-haiku-4-5": (1.00, 0.10, 1.25, 5.00),
}


def _run(fake, model, inp, read, create, out):
    """One complete() through the fake SDK inside track_usage; returns (text, aggregate)."""
    from llm.anthropic_client import AnthropicClient
    from llm.usage import current_usage, track_usage
    from utils.config import LLMSettings

    fake.USAGE = {"input_tokens": inp, "output_tokens": out,
                  "cache_read_input_tokens": read, "cache_creation_input_tokens": create}
    client = AnthropicClient(LLMSettings(provider="anthropic"))
    with track_usage("t"):
        text = client.complete(system="sys", user="question", model=model, purpose="test")
        agg = current_usage()
    return text, agg


@pytest.mark.criterion("C6")
def test_sonnet_call_adds_hand_calculated_cost(fake_anthropic):
    # oracle: spec C6 hand calculation with the Notes rates for claude-sonnet-4-6:
    #   (1000*3.00 + 4000*0.30 + 2000*3.75 + 500*15.00)/1e6 = (3000+1200+7500+7500)/1e6 = 0.0192
    text, agg = _run(fake_anthropic, "claude-sonnet-4-6", 1000, 4000, 2000, 500)
    assert text == "fake answer"
    assert len(fake_anthropic.CALLS) == 1
    assert fake_anthropic.CALLS[0]["model"] == "claude-sonnet-4-6"
    assert agg.calls == 1
    assert agg.cost_complete is True
    # tolerance: abs=1e-12 USD, spec C6
    assert agg.cost_usd == pytest.approx(0.0192, abs=1e-12)


@pytest.mark.criterion("C6")
def test_haiku_5_5_call_adds_hand_calculated_cost(fake_anthropic):
    # oracle: Notes rates for claude-haiku-5-5, hand calculation:
    #   input 2000, read 10000, creation 8000, output 1000:
    #   (2000*0.10 + 10000*0.01 + 8000*0.125 + 1000*0.50)/1e6 = (200+100+1000+500)/1e6 = 0.0018
    _, agg = _run(fake_anthropic, "claude-haiku-5-5", 2000, 10000, 8000, 1000)
    # tolerance: abs=1e-12 USD, spec C6
    assert agg.cost_usd == pytest.approx(0.0018, abs=1e-12)


@pytest.mark.criterion("C6")
@settings(max_examples=40, deadline=None, suppress_health_check=_HC)
@given(model=st.sampled_from(sorted(ANTHROPIC)),
       inp=st.integers(0, 500_000), read=st.integers(0, 500_000),
       create=st.integers(0, 500_000), out=st.integers(0, 100_000))
def test_property_aggregate_equals_call_cost(fake_anthropic, model, inp, read, create, out):
    # oracle: spec C6 — for any non-negative usage counts the aggregate cost equals call_cost
    # over the same counts (prompt = input + read + creation), and the Requirement formula
    # over the Notes rates (computed here).
    from llm.pricing import call_cost

    _, agg = _run(fake_anthropic, model, inp, read, create, out)
    prompt = inp + read + create
    want = call_cost(model, prompt_tokens=prompt, completion_tokens=out, cached_tokens=read,
                     cache_write_tokens=create)
    i, cr, cw, o = ANTHROPIC[model]
    hand = (inp * i + read * cr + create * cw + out * o) / 1e6
    # tolerance: abs=1e-12 USD, spec C6
    assert agg.cost_usd == pytest.approx(want, abs=1e-12)
    # tolerance: abs=1e-12 USD, spec C6
    assert agg.cost_usd == pytest.approx(hand, abs=1e-12)
    assert agg.prompt_tokens == prompt
