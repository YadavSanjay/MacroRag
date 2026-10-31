"""C5: the stream's answer event and the MCP query tools carry the same usage block."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

_TOOL_MODE = {"macro_query": "answer", "macro_synthesize_shock_vector": "synthesize"}


def _stream(auc, client, question):
    r = client.post("/query/stream", json={"question": question})
    assert r.status_code == 200, r.text
    events = auc.sse_events(r.text)
    assert "answer" in events, sorted(events)
    return events["answer"]


def _rest_usage(auc, root, mode, question):
    """usage of the matching POST /query on a fresh pipeline (None when absent)."""
    body = auc.post(auc.rest_client(auc.build(root, auc.PricedLLM(), l1=auc.mem_l1())),
                    mode=mode, question=question)
    return body.get("usage")


def _same_request(a, b):
    """Same calls, token totals and cost_usd (cached is compared separately)."""
    assert a is not None and b is not None, (a, b)
    for k in ("calls", "prompt_tokens", "completion_tokens", "cached_tokens",
              "total_tokens", "cost_complete"):
        assert a[k] == b[k], (k, a[k], b[k])
    # tolerance: rel=1e-9 as spec C2 (per-call float sums in another order)
    assert a["cost_usd"] == pytest.approx(b["cost_usd"], rel=1e-9, abs=0.0)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("caps", [False, True])
def test_stream_answer_event_usage(auc, tmp_path, caps):
    # oracle: the double's own call log and the committed price table (conftest
    # expected_usage); a repeat stream is an L1 hit: cached true, calls 0, cost 0.0.
    llm = auc.PricedLLM()
    client = auc.rest_client(auc.build(tmp_path, llm, l1=auc.mem_l1()), caps=caps)
    first = _stream(auc, client, auc.QUERY)
    assert first["cached"] is False and len(llm.log) > 0
    # tolerance: spec C2 rel=1e-9 (per-call float sums in another order)
    auc.assert_usage(first, auc.expected_usage(llm.log, cached=False), rel=1e-9)
    llm.reset()
    again = _stream(auc, client, auc.QUERY)
    assert again["cached"] is True and llm.log == []
    # tolerance: no call on an L1 hit, so 0.0 is exact (abs=0 in assert_usage)
    auc.assert_usage(again, auc.expected_usage([], cached=True), rel=1e-9)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("tool", ["macro_query", "macro_synthesize_shock_vector"])
def test_mcp_tool_usage(auc, tmp_path, tool):
    # oracle: the double's own call log and the committed price table (conftest
    # expected_usage). The repeat's query step is an L1 hit (cached true): macro_query
    # then makes no call; macro_synthesize_shock_vector still runs its synthesis pass.
    llm = auc.PricedLLM()
    mcp = auc.mcp_server(auc.build(tmp_path, llm, l1=auc.mem_l1()))
    first = auc.mcp_call(mcp, tool, {"question": auc.QUERY})
    assert first["cached"] is False and len(llm.log) > 0
    # tolerance: spec C2 rel=1e-9 (per-call float sums in another order)
    auc.assert_usage(first, auc.expected_usage(llm.log, cached=False), rel=1e-9)
    llm.reset()
    again = auc.mcp_call(mcp, tool, {"question": auc.QUERY})
    assert again["cached"] is True
    assert (len(llm.log) == 0) is (tool == "macro_query")
    # tolerance: spec C2 rel=1e-9 (per-call float sums in another order)
    auc.assert_usage(again, auc.expected_usage(llm.log, cached=True), rel=1e-9)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@pytest.mark.parametrize("surface", ["stream", "macro_query",
                                     "macro_synthesize_shock_vector"])
def test_surface_usage_equals_the_matching_query(auc, tmp_path, surface):
    # oracle: spec C5. Relation: the same request on a fresh pipeline through the
    # surface and through the matching POST /query reports the same calls, token
    # totals and cost_usd, and both are uncached.
    mode = _TOOL_MODE.get(surface, "answer")
    pipe = auc.build(tmp_path / "surface", auc.PricedLLM(), l1=auc.mem_l1())
    if surface == "stream":
        body = _stream(auc, auc.rest_client(pipe), auc.QUERY)
    else:
        body = auc.mcp_call(auc.mcp_server(pipe), surface, {"question": auc.QUERY})
    assert "usage" in body, sorted(body)
    rest = _rest_usage(auc, tmp_path / "rest", mode, auc.QUERY)
    _same_request(body["usage"], rest)
    assert body["usage"]["calls"] > 0
    assert (body["usage"]["cached"], rest["cached"]) == (False, False)


@pytest.mark.criterion("C5")
@settings(max_examples=12, deadline=None)
@given(surface=st.sampled_from(["stream", "macro_query", "macro_synthesize_shock_vector"]),
       question=st.sampled_from(["how did treasury yields behave",
                                 "what happened to treasury yields",
                                 "treasury yields across advanced economies",
                                 "did markets price cuts"]),
       repeats=st.integers(1, 2))
def test_property_surface_usage_matches_the_call_log(auc, surface, question, repeats):
    # oracle: the double's own per-request call log and the committed price table
    # (conftest expected_usage); each repeat is served by the answer cache (cached true).
    with tempfile.TemporaryDirectory() as root:
        llm = auc.PricedLLM()
        pipe = auc.build(root, llm, l1=auc.mem_l1())
        client = auc.rest_client(pipe) if surface == "stream" else None
        mcp = None if surface == "stream" else auc.mcp_server(pipe)
        for i in range(repeats):
            llm.reset()
            if surface == "stream":
                body = _stream(auc, client, question)
            else:
                body = auc.mcp_call(mcp, surface, {"question": question})
            assert body["cached"] is (i > 0)
            # tolerance: spec C2 rel=1e-9 (per-call float sums in another order)
            auc.assert_usage(body, auc.expected_usage(llm.log, cached=i > 0), rel=1e-9)
