"""C1: api.models.usage_to_dict builds the usage block of an aggregate."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

_KEYS = {"cost_usd", "cost_complete", "calls", "prompt_tokens", "completion_tokens",
         "cached_tokens", "total_tokens", "cached"}
_COUNTS = ("calls", "prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens")


def _aggregate(calls):
    """Open a track_usage block and record each (model, prompt, completion, cached) call."""
    from llm.usage import record_call, track_usage
    with track_usage("spec-c1") as agg:
        for model, p, c, k in calls:
            record_call(provider="test", model=model, prompt_tokens=p,
                        completion_tokens=c, cached_tokens=k, purpose="spec")
    return agg


def _check_types(block):
    assert set(block) == _KEYS, sorted(block)
    assert type(block["cost_usd"]) is float
    assert type(block["cost_complete"]) is bool
    assert type(block["cached"]) is bool
    for k in _COUNTS:
        assert type(block[k]) is int, (k, block[k])


@pytest.mark.criterion("C1")
def test_priced_aggregate_by_hand(auc):
    # oracle: hand calculation from configs/llm_pricing.yaml, gpt-5.4-mini at
    # input 0.75, output 4.50, cache_read 0.075 USD per 1M tokens. Calls:
    #   (1000 prompt, 200 completion, 0 cached):  1000*0.75 + 200*4.50 = 1650
    #   (2000 prompt, 500 completion, 400 cached): 1600*0.75 + 400*0.075 + 500*4.50
    #                                              = 1200 + 30 + 2250 = 3480
    # total 5130 / 1e6 = 0.00513 USD; tokens 3000 + 700 = 3700; cached 400.
    from api.models import usage_to_dict
    agg = _aggregate([("gpt-5.4-mini", 1000, 200, 0), ("gpt-5.4-mini", 2000, 500, 400)])
    block = usage_to_dict(agg, cached=False)
    _check_types(block)
    # tolerance: spec C1 floats to rel=1e-12 (binary rounding of the per-call sums)
    assert block["cost_usd"] == pytest.approx(0.00513, rel=1e-12)
    assert {k: block[k] for k in _KEYS - {"cost_usd"}} == {
        "cost_complete": True, "calls": 2, "prompt_tokens": 3000, "completion_tokens": 700,
        "cached_tokens": 400, "total_tokens": 3700, "cached": False}


@pytest.mark.criterion("C1")
def test_mixed_priced_and_unpriced_by_hand(auc):
    # oracle: spec Definitions + hand calculation. gpt-5.4-mini (100 prompt, 10
    # completion): 100*0.75 + 10*4.50 = 120 / 1e6 = 0.00012 USD; the unpriced call adds
    # no cost and makes cost_complete false; tokens 100+50 prompt, 10+5 completion.
    from api.models import usage_to_dict
    agg = _aggregate([("gpt-5.4-mini", 100, 10, 0), (auc.UNPRICED, 50, 5, 0)])
    block = usage_to_dict(agg, cached=True)
    _check_types(block)
    # tolerance: spec C1 floats to rel=1e-12 (binary rounding of the per-call sums)
    assert block["cost_usd"] == pytest.approx(0.00012, rel=1e-12)
    assert {k: block[k] for k in _KEYS - {"cost_usd"}} == {
        "cost_complete": False, "calls": 2, "prompt_tokens": 150, "completion_tokens": 15,
        "cached_tokens": 0, "total_tokens": 165, "cached": True}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("cached", [False, True])
def test_none_aggregate_is_free_and_complete(auc, cached):
    # oracle: spec Definitions — with agg None every count is 0, cost_usd is 0.0 and
    # cost_complete is true; cached is copied from the flag.
    from api.models import usage_to_dict
    block = usage_to_dict(None, cached=cached)
    _check_types(block)
    assert block == {"cost_usd": 0.0, "cost_complete": True, "calls": 0,
                     "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
                     "total_tokens": 0, "cached": cached}


_CALL = st.tuples(st.sampled_from(["gpt-5.4-mini", "gpt-5.4", "claude-opus-4-8",
                                   "zz-unpriced-model-x"]),
                  st.integers(0, 50_000), st.integers(0, 10_000),
                  st.integers(0, 50_000)).map(lambda t: (t[0], t[1], t[2], min(t[3], t[1])))


@pytest.mark.criterion("C1")
@settings(max_examples=50, deadline=None)
@given(calls=st.lists(_CALL, min_size=0, max_size=6), cached=st.booleans())
def test_property_block_matches_definitions(auc, calls, cached):
    # oracle: spec Definitions applied to the calls recorded in the test: counts summed
    # from the call list, cost the sum of llm.pricing.cost_usd over the priced calls
    # (committed configs/llm_pricing.yaml), cost_complete iff every call is priced.
    from api.models import usage_to_dict
    from llm.pricing import cost_usd
    block = usage_to_dict(_aggregate(calls), cached=cached)
    _check_types(block)
    costs = [cost_usd(m, prompt_tokens=p, completion_tokens=c, cached_tokens=k)
             for m, p, c, k in calls]
    prompt, completion = sum(p for _, p, _, _ in calls), sum(c for _, _, c, _ in calls)
    # tolerance: spec C1 floats to rel=1e-12; abs=0 so an all-unpriced/empty 0.0 is exact
    assert block["cost_usd"] == pytest.approx(sum(x for x in costs if x is not None),
                                              rel=1e-12, abs=0.0)
    assert math.isfinite(block["cost_usd"]) and block["cost_usd"] >= 0.0
    assert {k: block[k] for k in _KEYS - {"cost_usd"}} == {
        "cost_complete": all(x is not None for x in costs), "calls": len(calls),
        "prompt_tokens": prompt, "completion_tokens": completion,
        "cached_tokens": sum(k for _, _, _, k in calls),
        "total_tokens": prompt + completion, "cached": cached}
