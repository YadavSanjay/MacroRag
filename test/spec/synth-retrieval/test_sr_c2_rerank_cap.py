"""C2: synthesize reranks exactly min(N, F) documents in one call; answer unchanged."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _synth_counts(pipe, rec, query):
    rec.calls.clear()
    pipe.synthesize(query)
    return [len(call) for call in rec.calls]


def _answer_counts(pipe, rec, query):
    rec.calls.clear()
    pipe.answer(query)
    return [len(call) for call in rec.calls]


@pytest.mark.criterion("C2")
def test_synthesize_pool_capped_by_synthesis_setting(tmp_path, sr):
    # oracle: C2 rule by inspection. 16 close chunks + 4 scenario studies, all
    # passing the (empty) filter, fusion_limit 200 >= corpus: F = 20 distinct
    # candidates. Retriever rerank_candidates = 15, final_k = 3, R = 2 (default).
    #   N = 12 (default) -> one call on min(12, 20) = 12 docs
    #   N = None         -> retriever's own cap: min(15, 20) = 15
    #   N = 30           -> min(30, 20) = 20
    #   answer()         -> min(15, 20) = 15 whatever N is
    docs = [sr.close_doc(i) for i in range(16)] + [sr.scenario_doc(i) for i in range(4)]
    pipe, s, rec = sr.build_pipeline(tmp_path, docs, final_k=3, rerank_candidates=15)
    assert s.synthesis.rerank_candidates == 12

    assert _synth_counts(pipe, rec, sr.QUERY) == [12]
    assert _answer_counts(pipe, rec, sr.QUERY) == [15]

    s.synthesis.rerank_candidates = None
    assert _synth_counts(pipe, rec, sr.QUERY) == [15]

    s.synthesis.rerank_candidates = 30
    assert _synth_counts(pipe, rec, sr.QUERY) == [20]
    assert _answer_counts(pipe, rec, sr.QUERY) == [15]


@pytest.mark.criterion("C2")
@settings(max_examples=25)
@given(n_cap=st.integers(min_value=1, max_value=10),
       extra=st.integers(min_value=1, max_value=8),
       n_scen=st.integers(min_value=0, max_value=3),
       slots=st.integers(min_value=0, max_value=3),
       rc_extra=st.integers(min_value=1, max_value=10))
def test_rerank_count_is_min_n_f_reserved_inside(sr, n_cap, extra, n_scen, slots,
                                                 rc_extra):
    # oracle: C2 closed form over a generated corpus where every chunk passes the
    # filter: F = n_cap + extra (> N), split into n_scen studies and the rest close
    # chunks (>= 1). Retriever rerank_candidates = N + rc_extra (> N). Synthesize:
    # exactly one rerank call on min(N, F) = N docs, for any R (reserved studies
    # count inside N). answer(): min(rerank_candidates, F) docs, independent of N.
    f = n_cap + extra
    rc = n_cap + rc_extra
    n_scen = min(n_scen, f - 1)
    docs = ([sr.close_doc(i) for i in range(f - n_scen)]
            + [sr.scenario_doc(i) for i in range(n_scen)])
    with tempfile.TemporaryDirectory() as d:
        pipe, s, rec = sr.build_pipeline(d, docs, final_k=3, rerank_candidates=rc)
        s.synthesis.rerank_candidates = n_cap
        s.synthesis.scenario_study_slots = slots
        synth = _synth_counts(pipe, rec, sr.QUERY)
        ans = _answer_counts(pipe, rec, sr.QUERY)
    assert synth == [n_cap]
    assert ans == [min(rc, f)]
