"""C2: POST /query in answer mode carries the request's usage block."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("caps", [False, True])
def test_answer_usage_then_cached_hit(auc, tmp_path, caps):
    # oracle: the double's own call log and the committed price table (conftest
    # expected_usage): calls = len(log), tokens = calls x the fixed counts, cost_usd =
    # sum of llm.pricing.cost_usd(gpt-5.4-mini, 1200, 300); the repeat is an L1 hit:
    # cached true, calls 0, cost_usd 0.0 exactly (spec C2), caps disabled and enabled.
    llm = auc.PricedLLM()
    client = auc.rest_client(auc.build(tmp_path, llm, l1=auc.mem_l1()), caps=caps)
    first = auc.post(client)
    assert first["cached"] is False and first["refused"] is False
    assert len(llm.log) > 0
    expected = auc.expected_usage(llm.log, cached=False)
    assert expected["prompt_tokens"] == len(llm.log) * auc.PROMPT_TOKENS
    assert expected["completion_tokens"] == len(llm.log) * auc.COMPLETION_TOKENS
    # tolerance: spec C2 rel=1e-9 (per-call float sums may be added in another order)
    auc.assert_usage(first, expected, rel=1e-9)

    llm.reset()
    again = auc.post(client)
    assert again["cached"] is True and llm.log == []
    assert "usage" in again, sorted(again)
    # tolerance: abs=0 — spec C2 requires cost_usd 0.0 exactly on an L1 hit
    assert again["usage"]["cost_usd"] == pytest.approx(0.0, abs=0.0)
    assert again["usage"]["calls"] == 0
    # tolerance: an L1 hit makes no call, so 0.0 is exact (abs=0 in assert_usage)
    auc.assert_usage(again, auc.expected_usage([], cached=True), rel=1e-9)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@pytest.mark.parametrize("caps", [False, True])
def test_doubling_token_counts_doubles_cost(auc, tmp_path, caps):
    # oracle: spec C2 + llm.pricing.cost_usd being linear in token counts. Relation: the
    # same request on two fresh pipelines whose doubles report 1200/300 and 2400/600
    # tokens per call makes the same number of calls, and its tokens and cost_usd double.
    bodies = []
    for i, scale in enumerate((1, 2)):
        llm = auc.PricedLLM(prompt_tokens=scale * auc.PROMPT_TOKENS,
                            completion_tokens=scale * auc.COMPLETION_TOKENS)
        client = auc.rest_client(auc.build(tmp_path / str(i), llm, l1=auc.mem_l1()),
                                 caps=caps)
        bodies.append(auc.post(client))
    for b in bodies:
        assert "usage" in b, sorted(b)
    one, two = bodies[0]["usage"], bodies[1]["usage"]
    assert one["calls"] == two["calls"] > 0
    assert two["prompt_tokens"] == 2 * one["prompt_tokens"]
    assert two["total_tokens"] == 2 * one["total_tokens"]
    # tolerance: rel=1e-9 as spec C2 (float sums in different order)
    assert two["cost_usd"] == pytest.approx(2 * one["cost_usd"], rel=1e-9)
    assert one["cost_usd"] > 0.0


@pytest.mark.criterion("C2")
@settings(max_examples=15, deadline=None)
@given(question=st.sampled_from(["how did treasury yields behave",
                                 "what happened to treasury yields",
                                 "treasury yields across advanced economies",
                                 "did markets price cuts"]),
       prompt=st.integers(1, 100_000), completion=st.integers(1, 20_000),
       caps=st.booleans(), repeats=st.integers(1, 3))
def test_property_answer_usage_matches_the_call_log(auc, question, prompt, completion,
                                                    caps, repeats):
    # oracle: the double's own per-request call log and the committed price table
    # (conftest expected_usage); the first request is fresh, each repeat an L1 hit.
    with tempfile.TemporaryDirectory() as root:
        llm = auc.PricedLLM(prompt_tokens=prompt, completion_tokens=completion)
        client = auc.rest_client(auc.build(root, llm, l1=auc.mem_l1()), caps=caps)
        for i in range(repeats):
            llm.reset()
            body = auc.post(client, question=question)
            assert body["cached"] is (i > 0)
            # tolerance: spec C2 rel=1e-9 (per-call float sums in another order)
            auc.assert_usage(body, auc.expected_usage(llm.log, cached=i > 0), rel=1e-9)
