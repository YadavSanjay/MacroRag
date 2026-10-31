"""C1: reserved slots bring in scenario-study documents not already in W0."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _taiwan_close(srd):
    """Close group: scenario docs lexically identical to the query, 4 chunks in
    3 documents (close_00 has two chunks)."""
    q = srd.QUERY
    return [srd.make_doc("close_00", [q, q], scenario=True),
            srd.make_doc("close_01", [q], scenario=True),
            srd.make_doc("close_02", [q], scenario=True)]


@pytest.mark.criterion("C1")
def test_taiwan_shape_reserves_new_documents(tmp_path, srd):
    # oracle: C1 rule by inspection of the conftest construction (Taiwan shape).
    # K=4. Close group = 4 scenario chunks (3 docs) identical to the query; they
    # outrank every far chunk, so W0 = exactly those 4 chunks (all scenario
    # studies). Far group = 3 scenario docs (far_00 has 2 chunks), never in W0,
    # so D = {far_00, far_01, far_02}, |D| = 3. Synthesize cap N = 12 (default),
    # F = 8 chunks, P = min(12, 8) = 8. R = 2 (default): r = min(2, 3, 4, 8) = 2.
    # Expected: 4 winners = top 2 of W0 + 2 chunks from 2 distinct far docs, each
    # in the rerank pool. W0's scenario chunks do not use up the slots.
    far = [srd.make_doc("far_00", [srd.far_text(0, 0), srd.far_text(0, 1)], scenario=True),
           srd.make_doc("far_01", [srd.far_text(1, 0)], scenario=True),
           srd.make_doc("far_02", [srd.far_text(2, 0)], scenario=True)]
    pipe, s, rec = srd.build_pipeline(tmp_path, _taiwan_close(srd) + far, final_k=4)
    assert s.synthesis.scenario_study_slots == 2

    w0, _, _ = srd.run(pipe, rec, 0)
    close_ids = {srd.chunk_id("close_00", 0), srd.chunk_id("close_00", 1),
                 srd.chunk_id("close_01"), srd.chunk_id("close_02")}
    assert set(w0) == close_ids

    w, docs, pool = srd.run(pipe, rec, 2)
    new = [c for c in w if c not in w0]
    new_docs = [docs[c] for c in new]
    assert len(w) == 4
    assert set(new_docs) <= {"far_00", "far_01", "far_02"}
    assert len(set(new_docs)) >= 2
    assert len(new_docs) == len(set(new_docs))
    assert set(new) <= pool
    assert set(w) & set(w0) == set(w0[:2])


@pytest.mark.criterion("C1")
def test_multi_chunk_document_takes_one_slot(tmp_path, srd):
    # oracle: C1 rule by inspection. Same close group as above (W0 = its 4
    # chunks), K=4. Far group: far_00 with 3 chunks, far_01 with 1 chunk, so
    # D = {far_00, far_01}, |D| = 2. R = 3, P = min(12, 8) = 8:
    # r = min(3, 2, 4, 8) = 2. Winners count 4 with >= 2 distinct D documents
    # and the top |W0| - r = 2 of W0, hence exactly one chunk of far_00, one
    # of far_01, and W0[:2]; a second far_00 chunk may not fill the third slot.
    far = [srd.make_doc("far_00", [srd.far_text(0, j) for j in range(3)], scenario=True),
           srd.make_doc("far_01", [srd.far_text(1, 0)], scenario=True)]
    pipe, _, rec = srd.build_pipeline(tmp_path, _taiwan_close(srd) + far, final_k=4)
    w0, _, _ = srd.run(pipe, rec, 0)
    assert len(w0) == 4
    assert all(srd.doc_of(c).startswith("close_") for c in w0)

    w, docs, pool = srd.run(pipe, rec, 3)
    new = [c for c in w if c not in w0]
    assert len(w) == 4
    assert sorted(docs[c] for c in new) == ["far_00", "far_01"]
    assert set(new) <= pool
    assert set(w) & set(w0) == set(w0[:2])


@pytest.mark.criterion("C1")
@settings(max_examples=25, deadline=None)
@given(k=st.integers(min_value=1, max_value=4),
       close_sizes=st.lists(st.integers(min_value=1, max_value=2), min_size=1, max_size=4),
       far_sizes=st.lists(st.integers(min_value=1, max_value=3), min_size=1, max_size=4),
       slots=st.integers(min_value=1, max_value=4),
       cap_extra=st.integers(min_value=0, max_value=8))
def test_reserved_documents_follow_rule(srd, k, close_sizes, far_sizes, slots, cap_extra):
    # oracle: C1 rule as a closed form over the generated corpus, with D taken
    # from its definition. Close scenario docs (text = query, sizes close_sizes)
    # are padded with single-chunk close scenario docs so there are >= K close
    # chunks; every close chunk outranks every far chunk, so W0 holds only close
    # (scenario) chunks. Synthesize cap N = K + cap_extra >= K, F = all chunks,
    # P = min(N, F), |W0| = min(K, P) = K. D = scenario docs (all docs here)
    # with no chunk in W0; r = min(R, |D|, K, P). Winners: |W0| chunks, >= r
    # distinct D docs among the non-W0 winners (one chunk each), all in the
    # pool, plus the top |W0| - r of W0.
    q = srd.QUERY
    sizes = list(close_sizes)
    while sum(sizes) < k:
        sizes.append(1)
    docs = [srd.make_doc(f"close_{i:02d}", [q] * n, scenario=True)
            for i, n in enumerate(sizes)]
    docs += [srd.make_doc(f"far_{i:02d}", [srd.far_text(i, j) for j in range(n)],
                          scenario=True) for i, n in enumerate(far_sizes)]
    all_doc_ids = {doc["doc_id"] for doc in docs}
    f = sum(len(doc["chunks"]) for doc in docs)
    cap = k + cap_extra
    p = min(cap, f)
    with tempfile.TemporaryDirectory() as d:
        pipe, s, rec = srd.build_pipeline(d, docs, final_k=k)
        s.synthesis.rerank_candidates = cap
        w0, _, _ = srd.run(pipe, rec, 0)
        w, wdocs, pool = srd.run(pipe, rec, slots)
    assert len(w0) == min(k, p)
    assert all(srd.doc_of(c).startswith("close_") for c in w0)
    d_set = all_doc_ids - {srd.doc_of(c) for c in w0}
    r = min(slots, len(d_set), k, p)
    new = [c for c in w if c not in w0]
    new_docs = [wdocs[c] for c in new]
    assert len(w) == len(w0)
    assert set(new_docs) <= d_set
    assert len(new_docs) == len(set(new_docs))
    assert len(set(new_docs)) >= r
    assert set(new) <= pool
    assert set(w0[: len(w0) - r]) <= set(w)
