"""C1: reserved scenario-study winner slots in synthesize."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from utils.schema import MetaFilter


def _winners(pipe, query, base_filter=None):
    return [c.chunk_id for c in pipe.synthesize(query, base_filter=base_filter)
            .query.chunks]


def _run(pipe, rec, slots, query, base_filter=None):
    """(winner ids, rerank pool ids) for one synthesize call with R = slots."""
    pipe.s.synthesis.scenario_study_slots = slots
    rec.calls.clear()
    winners = _winners(pipe, query, base_filter)
    pool = {cid for call in rec.calls for cid in call}
    return winners, pool


@pytest.mark.criterion("C1")
def test_default_slots_reserve_two_studies_replacing_lowest(tmp_path, sr):
    # oracle: C1 rule by inspection of the conftest construction. 14 close chunks,
    # 3 reservable scenario studies, K=4, N=12 (default), R=2 (default).
    # F = 17, P = min(12, 17) = 12. Every close chunk outranks every study, so
    # with R=0 the winners are the top 4 close chunks and the pool (top 12 fused)
    # holds close chunks only. With R=2: min(R, S, K, P) = min(2, 3, 4, 12) = 2
    # studies, total winners still 4, so they replace the 2 lowest-ranked R=0
    # winners: the other winners are the first 2 of the R=0 list.
    docs = [sr.close_doc(i) for i in range(14)] + [sr.scenario_doc(i) for i in range(3)]
    pipe, s, rec = sr.build_pipeline(tmp_path, docs, final_k=4)
    assert s.synthesis.scenario_study_slots == 2

    base_w, base_pool = _run(pipe, rec, 0, sr.QUERY)
    w, pool = _run(pipe, rec, 2, sr.QUERY)
    reserved = [c for c in w if sr.is_scenario(c)]
    others = [c for c in w if not sr.is_scenario(c)]
    assert len(w) == 4
    assert len(reserved) == 2
    assert set(reserved) <= pool

    assert len(base_w) == 4
    assert not any(sr.is_scenario(c) for c in base_w)
    assert not any(sr.is_scenario(c) for c in base_pool)
    assert set(others) == set(base_w[:2])


@pytest.mark.criterion("C1")
def test_only_reservable_studies_fill_slots(tmp_path, sr):
    # oracle: C1 definition of "reservable" (must pass the synthesize filter), by
    # inspection. Caller filter publishers=["Desk"]. 10 close chunks (Desk), one
    # Desk study and two EBA studies: S = 1 (EBA studies fail the publisher facet).
    # K=3, R=2 (default), P = min(12, F=11) = 11. Expected min(2, 1, 3, 11) = 1:
    # exactly the Desk study among 3 winners; no EBA study in winners or pool.
    docs = ([sr.close_doc(i) for i in range(10)]
            + [sr.scenario_doc(0, publisher="Desk"),
               sr.scenario_doc(1, publisher="EBA"),
               sr.scenario_doc(2, publisher="EBA")])
    pipe, _, rec = sr.build_pipeline(tmp_path, docs, final_k=3)
    w, pool = _run(pipe, rec, 2, sr.QUERY, MetaFilter(publishers=["Desk"]))
    assert len(w) == 3
    assert [c for c in w if sr.is_scenario(c)] == [sr.chunk_id("scen_00")]
    assert sr.chunk_id("scen_01") not in pool
    assert sr.chunk_id("scen_02") not in pool


@pytest.mark.criterion("C1")
@settings(max_examples=25)
@given(k=st.integers(min_value=1, max_value=5),
       extra_close=st.integers(min_value=0, max_value=4),
       n_scen=st.integers(min_value=1, max_value=4),
       slots=st.integers(min_value=1, max_value=5),
       n_cap=st.integers(min_value=1, max_value=15))
def test_reserved_count_follows_rule(sr, k, extra_close, n_scen, slots, n_cap):
    # oracle: C1 rule as a closed form over the generated corpus. M = k +
    # extra_close close chunks (so >= K close chunks: R=0 winners hold no study),
    # S = n_scen reservable studies, F = M + S, P = min(N, F). Winners count
    # W = min(K, P) for any R; reserved = min(R, S, K, P); others = the first
    # W - reserved of the R=0 winners; every reserved chunk is in the pool.
    m = k + extra_close
    p = min(n_cap, m + n_scen)
    w_expected = min(k, p)
    r_expected = min(slots, n_scen, k, p)
    docs = [sr.close_doc(i) for i in range(m)] + [sr.scenario_doc(i) for i in range(n_scen)]
    with tempfile.TemporaryDirectory() as d:
        pipe, s, rec = sr.build_pipeline(d, docs, final_k=k)
        s.synthesis.rerank_candidates = n_cap
        base_w, _ = _run(pipe, rec, 0, sr.QUERY)
        w, pool = _run(pipe, rec, slots, sr.QUERY)
    reserved = [c for c in w if sr.is_scenario(c)]
    others = [c for c in w if not sr.is_scenario(c)]
    assert len(base_w) == w_expected
    assert not any(sr.is_scenario(c) for c in base_w)
    assert len(w) == w_expected
    assert len(reserved) == r_expected
    assert set(others) == set(base_w[: w_expected - r_expected])
    assert set(reserved) <= pool
