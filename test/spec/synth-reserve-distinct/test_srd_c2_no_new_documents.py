"""C2: with no new documents (D empty), synthesize's winners equal W0."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.mark.criterion("C2")
def test_document_already_in_w0_adds_nothing(tmp_path, srd):
    # oracle: C2 by inspection. K=4. Close chunks (text = query): plain_00..02
    # (non-scenario) and study_00::000 (scenario); study_00 has a second, far
    # chunk study_00::001. The 4 close chunks outrank every far chunk, so
    # W0 = those 4. The only reservable document, study_00, has a chunk in W0,
    # so D is empty: with R = 2 the winners must equal W0 exactly (same ids,
    # same order); study_00::001 may not displace a base winner.
    # Note: the spec's literal C2 corpus (close group all scenario studies, far
    # group removed) already gives winners == W0 on the current code; this
    # variant keeps D empty by the spec's document definition while the current
    # chunk-counting rule would add study_00::001.
    q = srd.QUERY
    docs = [srd.make_doc(f"plain_{i:02d}", [q], scenario=False) for i in range(3)]
    docs.append(srd.make_doc("study_00", [q, srd.far_text(0, 1)], scenario=True))
    docs += [srd.make_doc(f"plainfar_{i:02d}", [srd.far_text(5 + i, 0)], scenario=False)
             for i in range(2)]
    pipe, s, rec = srd.build_pipeline(tmp_path, docs, final_k=4)
    assert s.synthesis.scenario_study_slots == 2
    w0, _, _ = srd.run(pipe, rec, 0)
    assert set(w0) == {srd.chunk_id("plain_00"), srd.chunk_id("plain_01"),
                       srd.chunk_id("plain_02"), srd.chunk_id("study_00", 0)}
    w, _, _ = srd.run(pipe, rec, 2)
    assert w == w0


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=25, deadline=None)
@given(n_plain=st.integers(min_value=1, max_value=3),
       study_far=st.lists(st.integers(min_value=0, max_value=2), min_size=1, max_size=3),
       slots=st.integers(min_value=1, max_value=5),
       cap_extra=st.integers(min_value=0, max_value=6))
def test_slots_do_not_change_winners_when_d_empty(srd, n_plain, study_far, slots,
                                                   cap_extra):
    # oracle: C2 metamorphic relation: when D is empty, changing R from 0 to any
    # R > 0 leaves the winner list unchanged. Construction: n_plain non-scenario
    # close chunks plus one close chunk per scenario doc (scenario doc i also
    # has study_far[i] far chunks). K = number of close chunks and synthesize
    # cap N = K + cap_extra >= K, so W0 = exactly the close chunks and every
    # scenario document has a chunk in W0: D is empty by definition.
    q = srd.QUERY
    docs = [srd.make_doc(f"plain_{i:02d}", [q], scenario=False) for i in range(n_plain)]
    docs += [srd.make_doc(f"study_{i:02d}", [q] + [srd.far_text(i, j + 1) for j in range(n)],
                          scenario=True) for i, n in enumerate(study_far)]
    close_ids = {srd.chunk_id(doc["doc_id"], 0) for doc in docs}
    k = len(close_ids)
    with tempfile.TemporaryDirectory() as d:
        pipe, s, rec = srd.build_pipeline(d, docs, final_k=k)
        s.synthesis.rerank_candidates = k + cap_extra
        w0, _, _ = srd.run(pipe, rec, 0)
        w, _, _ = srd.run(pipe, rec, slots)
    assert set(w0) == close_ids
    assert w == w0
