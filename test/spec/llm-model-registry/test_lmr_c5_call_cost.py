"""C5: call_cost prices cache reads and cache writes from the rate card."""

from __future__ import annotations

import math

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
# Every committed card row (the OpenAI rows carry no cache_write, spec Notes).
ALL_ROWS = tuple(ANTHROPIC) + ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-6-sol",
                               "gpt-6-luna")
UNPRICED = "zz-unpriced-c5"


def _formula(rates, p, o, r, w):
    """Spec Requirement formula."""
    inp, cr, cw, out = rates
    return ((p - r - w) * inp + r * cr + w * cw + o * out) / 1e6


@pytest.mark.criterion("C5")
def test_hand_calculated_anthropic_costs():
    # oracle: spec Notes rates and hand calculations:
    #   sonnet-4-6 P=10000 R=4000 W=2000 O=500: uncached 4000.
    #     (4000*3.00 + 4000*0.30 + 2000*3.75 + 500*15.00)/1e6 = (12000+1200+7500+7500)/1e6 = 0.0282
    #   haiku-5-5 P=1e6 R=2e5 W=3e5 O=1e5: uncached 5e5.
    #     (500000*0.10 + 200000*0.01 + 300000*0.125 + 100000*0.50)/1e6
    #     = (50000+2000+37500+50000)/1e6 = 0.1395
    #   opus-5-5 P=10000 W=10000: (10000*5.00)/1e6 = 0.05
    from llm.pricing import call_cost

    # tolerance: abs=1e-12 USD, spec C5
    assert call_cost("claude-sonnet-4-6", prompt_tokens=10000, completion_tokens=500,
                     cached_tokens=4000, cache_write_tokens=2000) \
        == pytest.approx(0.0282, abs=1e-12)
    # tolerance: abs=1e-12 USD, spec C5
    assert call_cost("claude-haiku-5-5", prompt_tokens=1_000_000, completion_tokens=100_000,
                     cached_tokens=200_000, cache_write_tokens=300_000) == pytest.approx(
                         0.1395, abs=1e-12)
    # tolerance: abs=1e-12 USD, spec C5
    assert call_cost("claude-opus-5-5", prompt_tokens=10000,
                     cache_write_tokens=10000) == pytest.approx(0.05, abs=1e-12)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("model", sorted(ANTHROPIC))
def test_each_anthropic_row_matches_notes(model):
    # oracle: spec Requirement formula over the Notes rates, P=20000 R=5000 W=7000 O=3000,
    # computed in the test by _formula.
    from llm.pricing import call_cost

    want = _formula(ANTHROPIC[model], 20000, 3000, 5000, 7000)
    got = call_cost(model, prompt_tokens=20000, completion_tokens=3000, cached_tokens=5000,
                    cache_write_tokens=7000)
    # tolerance: abs=1e-12 USD, spec C5
    assert got == pytest.approx(want, abs=1e-12)
    assert want > 0


@pytest.mark.criterion("C5")
def test_cache_write_defaults_to_input_for_openai_rows():
    # oracle: spec Requirement ("cache_write defaults to input when absent") + Notes (OpenAI
    # rows get no cache_write) + committed card gpt-5.4 input 2.50:
    #   P=1000 W=1000: (1000*2.50)/1e6 = 0.0025
    from llm.pricing import call_cost

    # tolerance: abs=1e-12 USD, spec C5
    assert call_cost("gpt-5.4", prompt_tokens=1000,
                     cache_write_tokens=1000) == pytest.approx(0.0025, abs=1e-12)


@pytest.mark.criterion("C5")
def test_unpriced_is_none_and_bad_counts_raise():
    # oracle: spec C5 — None for an unpriced model; ValueError for a negative count or
    # R + W > P. A priced case alongside: sonnet-4-6 O=1000 -> 1000*15.00/1e6 = 0.015.
    from llm.pricing import call_cost

    assert call_cost(UNPRICED, prompt_tokens=100, completion_tokens=10) is None
    # tolerance: abs=1e-12 USD, spec C5
    assert call_cost("claude-sonnet-4-6", completion_tokens=1000) == pytest.approx(0.015,
                                                                                   abs=1e-12)
    bad = [dict(prompt_tokens=-1), dict(completion_tokens=-1),
           dict(prompt_tokens=10, cached_tokens=-1), dict(prompt_tokens=10, cache_write_tokens=-1),
           dict(prompt_tokens=10, cached_tokens=6, cache_write_tokens=5),
           dict(prompt_tokens=10, cached_tokens=11), dict(prompt_tokens=10, cache_write_tokens=11)]
    for kw in bad:
        with pytest.raises(ValueError):
            call_cost("claude-sonnet-4-6", **kw)


_COUNT = st.integers(min_value=0, max_value=2_000_000)


@pytest.mark.criterion("C5")
@settings(max_examples=80, deadline=None, suppress_health_check=_HC)
@given(model=st.sampled_from(sorted(ANTHROPIC)), p=_COUNT, o=_COUNT, data=st.data())
def test_property_formula_over_notes_rates(model, p, o, data):
    # oracle: spec Requirement formula over the Notes rates (computed in _formula).
    from llm.pricing import call_cost

    r = data.draw(st.integers(0, p))
    w = data.draw(st.integers(0, p - r))
    got = call_cost(model, prompt_tokens=p, completion_tokens=o, cached_tokens=r,
                    cache_write_tokens=w)
    # tolerance: abs=1e-12 USD, spec C5 (costs here are below 100 USD)
    assert got == pytest.approx(_formula(ANTHROPIC[model], p, o, r, w), abs=1e-12)
    assert math.isfinite(got)
    # A fixed nonzero case in every example, so a constant stub fails each one.
    # tolerance: abs=1e-12 USD, spec C5 (hand value 0.0282, see the point test)
    assert call_cost("claude-sonnet-4-6", prompt_tokens=10000, completion_tokens=500,
                     cached_tokens=4000, cache_write_tokens=2000) \
        == pytest.approx(0.0282, abs=1e-12)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@settings(max_examples=80, deadline=None, suppress_health_check=_HC)
@given(model=st.sampled_from(ALL_ROWS), p=_COUNT, o=_COUNT, data=st.data())
def test_no_cache_writes_equals_cost_usd(model, p, o, data):
    # oracle: spec C5 / Requirement "Unchanged". Relation: with W == 0, call_cost equals the
    # existing llm.pricing.cost_usd over the same counts, for every committed row.
    from llm.pricing import call_cost, cost_usd

    r = data.draw(st.integers(0, p))
    want = cost_usd(model, prompt_tokens=p, completion_tokens=o, cached_tokens=r)
    got = call_cost(model, prompt_tokens=p, completion_tokens=o, cached_tokens=r)
    got0 = call_cost(model, prompt_tokens=p, completion_tokens=o, cached_tokens=r,
                     cache_write_tokens=0)
    assert want is not None
    # tolerance: abs=1e-12 USD, spec C5 (same formula, possibly another summation order)
    assert got == pytest.approx(want, abs=1e-12)
    # tolerance: abs=1e-12 USD, spec C5
    assert got0 == pytest.approx(want, abs=1e-12)
    # tolerance: abs=1e-12 USD, spec C5 (sonnet-4-6 O=1000: 1000*15.00/1e6 = 0.015)
    assert call_cost("claude-sonnet-4-6", completion_tokens=1000) == pytest.approx(0.015,
                                                                                   abs=1e-12)
