"""C1: synthesize never takes the plan-execute route."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.mark.criterion("C1")
def test_synthesize_uses_normal_retrieval_with_filters_and_card(srf, tmp_path):
    # oracle: spec C1 — the agent would plan (quantitative question, scripted planner),
    # yet synthesize takes the normal path: route not PLAN_EXECUTE, planner never called,
    # every chunk from an allowed publisher, and the episode card retrieved.
    pipe, llm = srf.build(tmp_path)
    syn = pipe.synthesize(srf.QUERY, base_filter=srf.meta_filter([srf.FED, srf.CARD]))
    q = syn.query
    assert q.analysis.route != "PLAN_EXECUTE"
    assert srf.planner_calls(llm) == 0
    pubs = {c.payload.get("publisher") for c in q.chunks}
    assert pubs <= {srf.FED, srf.CARD}, pubs
    assert "market_episode_taper_tantrum::000" in {c.chunk_id for c in q.chunks}


@pytest.mark.criterion("C1")
@settings(max_examples=6, deadline=None)
@given(allowed=st.sampled_from([["Federal Reserve", "MacroRAG (derived from FRED)"],
                                ["MacroRAG (derived from FRED)"],
                                ["Bank for International Settlements",
                                 "MacroRAG (derived from FRED)"]]))
def test_property_synthesize_chunks_always_inside_the_filter(srf, allowed):
    # oracle: spec C1 computed in the test: for any publisher filter that admits the card,
    # synthesize returns only allowed publishers, includes the card, never plans.
    with tempfile.TemporaryDirectory() as root:
        pipe, llm = srf.build(root)
        q = pipe.synthesize(srf.QUERY, base_filter=srf.meta_filter(allowed)).query
    assert {c.payload.get("publisher") for c in q.chunks} <= set(allowed)
    assert "market_episode_taper_tantrum::000" in {c.chunk_id for c in q.chunks}
    assert srf.planner_calls(llm) == 0 and q.analysis.route != "PLAN_EXECUTE"
