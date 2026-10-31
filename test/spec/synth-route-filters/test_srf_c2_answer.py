"""C2: on the plan-execute route, answer keeps only chunks matching the caller's filter."""

from __future__ import annotations

import tempfile

import pytest


@pytest.mark.criterion("C2")
def test_answer_agent_route_applies_the_filter(srf, tmp_path):
    # oracle: spec C2 — the agent plans (route PLAN_EXECUTE) and its executor retrieves
    # both notes, but with publishers=[Federal Reserve] only Fed chunks are returned.
    pipe, llm = srf.build(tmp_path)
    res = pipe.answer(srf.QUERY, base_filter=srf.meta_filter([srf.FED]))
    assert res.analysis.route == "PLAN_EXECUTE"
    assert srf.planner_calls(llm) == 1
    assert res.chunks, "no chunks returned"
    assert {c.payload.get("publisher") for c in res.chunks} == {srf.FED}


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_unfiltered_answer_unchanged_filtered_is_a_subset(srf):
    # oracle: spec C2. Relation: without a filter the agent's chunks come back unchanged
    # (both note publishers present); with publishers=[BIS] the result is exactly the
    # unfiltered chunks whose publisher is BIS.
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        pa, _ = srf.build(a)
        pb, _ = srf.build(b)
        free = pa.answer(srf.QUERY)
        only_bis = pb.answer(srf.QUERY, base_filter=srf.meta_filter([srf.BIS]))
    assert free.analysis.route == only_bis.analysis.route == "PLAN_EXECUTE"
    free_pubs = {c.payload.get("publisher") for c in free.chunks}
    assert {srf.FED, srf.BIS} <= free_pubs, free_pubs
    assert [c.chunk_id for c in only_bis.chunks] == \
        [c.chunk_id for c in free.chunks if c.payload.get("publisher") == srf.BIS]
