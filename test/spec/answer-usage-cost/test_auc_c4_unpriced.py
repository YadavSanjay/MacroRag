"""C4: an unpriced model makes cost_complete false and cost_usd a lower bound."""

from __future__ import annotations

import math
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _run(auc, root, llm, mode="answer"):
    client = auc.rest_client(auc.build(root, llm, l1=auc.mem_l1()))
    llm.reset()
    return auc.post(client, mode=mode)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("mode", ["answer", "synthesize"])
def test_all_unpriced_is_free_but_incomplete(auc, tmp_path, mode):
    # oracle: spec Definitions + C4 — no call is priced, so cost_usd is 0.0 exactly and
    # cost_complete false; calls and tokens still come from the double's own log.
    llm = auc.PricedLLM(model=auc.UNPRICED)
    body = _run(auc, tmp_path, llm, mode)
    assert len(llm.log) > 0
    expected = auc.expected_usage(llm.log, cached=False)
    # tolerance: abs=0 — no priced call, so spec C4 requires 0.0 exactly
    assert expected["cost_usd"] == pytest.approx(0.0, abs=0.0)
    assert expected["cost_complete"] is False
    # tolerance: no priced call, so 0.0 is exact (abs=0 in assert_usage)
    auc.assert_usage(body, expected, rel=1e-9)
    # tolerance: abs=0 — spec C4 "0.0 when none is priced"
    assert body["usage"]["cost_usd"] == pytest.approx(0.0, abs=0.0)


@pytest.mark.criterion("C4")
def test_mixed_counts_only_the_priced_calls(auc, tmp_path):
    # oracle: the double's own log and llm.pricing.cost_usd over the committed table:
    # even-numbered calls under gpt-5.4-mini, odd ones unpriced; cost_usd is the sum
    # over the priced calls only and cost_complete is false (spec C4). Synthesize mode,
    # so the request makes more than one call (query step + synthesis pass).
    llm = auc.PricedLLM(model_for=lambda i: auc.PRICED if i % 2 == 0 else auc.UNPRICED)
    body = _run(auc, tmp_path, llm, "synthesize")
    models = {e["model"] for e in llm.log}
    assert models == {auc.PRICED, auc.UNPRICED}, models
    expected = auc.expected_usage(llm.log, cached=False)
    assert expected["cost_usd"] > 0.0 and expected["cost_complete"] is False
    # tolerance: spec C2 rel=1e-9 (per-call float sums in another order)
    auc.assert_usage(body, expected, rel=1e-9)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_pricing_the_unpriced_calls_only_raises_cost(auc, tmp_path):
    # oracle: spec C4 (cost_usd is a lower bound when incomplete). Relation: the same
    # request with every call priced makes the same calls and tokens, has cost_usd at
    # least the mixed run's (strictly more, since unpriced calls are now priced), and
    # cost_complete flips from false to true. Synthesize mode: more than one call.
    mixed = _run(auc, tmp_path / "mixed", auc.PricedLLM(
        model_for=lambda i: auc.PRICED if i % 2 == 0 else auc.UNPRICED), "synthesize")
    priced = _run(auc, tmp_path / "priced", auc.PricedLLM(), "synthesize")
    for b in (mixed, priced):
        assert "usage" in b, sorted(b)
    m, p = mixed["usage"], priced["usage"]
    assert (m["calls"], m["total_tokens"]) == (p["calls"], p["total_tokens"])
    assert m["calls"] >= 2
    assert p["cost_usd"] > m["cost_usd"] >= 0.0
    assert (m["cost_complete"], p["cost_complete"]) == (False, True)


@pytest.mark.criterion("C4")
@settings(max_examples=15, deadline=None)
@given(pattern=st.lists(st.booleans(), min_size=1, max_size=6),
       mode=st.sampled_from(["answer", "synthesize"]))
def test_property_cost_is_the_priced_sum_finite_and_nonnegative(auc, pattern, mode):
    # oracle: spec Definitions + C4 applied to the double's own log: call i is priced
    # iff pattern[i % len(pattern)]; cost_usd = sum of llm.pricing.cost_usd over priced
    # calls, cost_complete iff all priced; cost_usd always finite and >= 0.
    with tempfile.TemporaryDirectory() as root:
        llm = auc.PricedLLM(model_for=lambda i: (auc.PRICED if pattern[i % len(pattern)]
                                                 else auc.UNPRICED))
        body = _run(auc, root, llm, mode)
        expected = auc.expected_usage(llm.log, cached=False)
    # tolerance: spec C2 rel=1e-9 (per-call float sums in another order)
    auc.assert_usage(body, expected, rel=1e-9)
    cost = body["usage"]["cost_usd"]
    assert math.isfinite(cost) and cost >= 0.0
