"""C3: POST /query in synthesize mode covers the query step and the synthesis pass."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _client(auc, root, llm, caps=False):
    return auc.rest_client(auc.build(root, llm, l1=auc.mem_l1()), caps=caps)


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("caps", [False, True])
def test_synthesize_usage_then_cached_query_step(auc, tmp_path, caps):
    # oracle: the double's own call log and the committed price table (conftest
    # expected_usage). The first request's usage covers every call it made (query step
    # + synthesis pass); the repeat's query step is an L1 hit, but its synthesis pass
    # still calls the model (the shock vector is never cached), so it reports those
    # calls and their cost with cached true (spec C3).
    llm = auc.PricedLLM()
    client = _client(auc, tmp_path, llm, caps)
    first = auc.post(client, mode="synthesize")
    assert first["cached"] is False and first["refused"] is False
    first_calls = len(llm.log)
    # tolerance: spec C2/C3 rel=1e-9 (per-call float sums in another order)
    auc.assert_usage(first, auc.expected_usage(llm.log, cached=False), rel=1e-9)

    llm.reset()
    again = auc.post(client, mode="synthesize")
    assert again["cached"] is True
    assert 0 < len(llm.log) < first_calls
    # tolerance: spec C2/C3 rel=1e-9 (per-call float sums in another order)
    auc.assert_usage(again, auc.expected_usage(llm.log, cached=True), rel=1e-9)
    assert again["usage"]["cost_usd"] > 0.0


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_synthesize_costs_more_than_its_cached_repeat(auc, tmp_path):
    # oracle: spec C3. Relation: the fresh synthesize covers its query step and the
    # synthesis pass, a cached repeat only the synthesis pass, so the fresh one has
    # strictly more calls, tokens and cost; two cached repeats report identical usage
    # (same calls, tokens, cost, cached true).
    llm = auc.PricedLLM()
    client = _client(auc, tmp_path, llm)
    bodies = [auc.post(client, mode="synthesize") for _ in range(3)]
    for b in bodies:
        assert "usage" in b, sorted(b)
    fresh, rep1, rep2 = (b["usage"] for b in bodies)
    assert fresh["calls"] > rep1["calls"] > 0
    assert fresh["total_tokens"] > rep1["total_tokens"]
    assert fresh["cost_usd"] > rep1["cost_usd"] > 0.0
    assert rep1 == rep2
    assert (fresh["cached"], rep1["cached"]) == (False, True)


@pytest.mark.criterion("C3")
@settings(max_examples=15, deadline=None)
@given(prompt=st.integers(1, 100_000), completion=st.integers(1, 20_000),
       caps=st.booleans(), repeats=st.integers(1, 3))
def test_property_synthesize_usage_matches_the_call_log(auc, prompt, completion, caps,
                                                        repeats):
    # oracle: the double's own per-request call log and the committed price table
    # (conftest expected_usage); each repeat's query step is an L1 hit (cached true).
    with tempfile.TemporaryDirectory() as root:
        llm = auc.PricedLLM(prompt_tokens=prompt, completion_tokens=completion)
        client = _client(auc, root, llm, caps)
        for i in range(repeats):
            llm.reset()
            body = auc.post(client, mode="synthesize")
            assert body["cached"] is (i > 0)
            assert len(llm.log) > 0
            # tolerance: spec C3 rel=1e-9 (per-call float sums in another order)
            auc.assert_usage(body, auc.expected_usage(llm.log, cached=i > 0), rel=1e-9)
