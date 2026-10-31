"""C1: retag_documents rewrites only the chunk tags of one source's documents."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ingest.common.retag import retag_documents


@pytest.mark.criterion("C1")
def test_retags_every_chunk_and_keeps_everything_else(rt):
    # oracle: C1 by hand. Two speeches docs (2 and 3 chunks) with the old tags are
    # retagged to the new list and entity ["ai"]: 2 docs read, 2 changed, 5 chunks;
    # parsed back, each equals the original except for the chunk tags.
    store = rt.CountingStore()
    docs = [rt.doc("speeches", 1, chunks=2), rt.doc("speeches", 2, chunks=3)]
    for d in docs:
        rt.put(store, d)
    store.writes.clear()
    counts = retag_documents(store, tier=1, source_id="speeches",
                             factor_tags=list(rt.NEW_FACTOR), entity_tags=["ai"])
    assert counts == {"docs": 2, "changed_docs": 2, "changed_chunks": 5}
    assert sorted(store.writes) == sorted(rt.key(d) for d in docs)
    for d in docs:
        got = rt.load(store, rt.key(d))
        assert [c.metadata.factor_tags for c in got.chunks] == [rt.NEW_FACTOR] * len(d.chunks)
        assert [c.metadata.entity_tags for c in got.chunks] == [["ai"]] * len(d.chunks)
        assert rt.without_tags(got) == rt.without_tags(d)


@pytest.mark.criterion("C1")
def test_written_format_and_partial_change(rt):
    # oracle: C1 — the written text is the processed doc store's format,
    # model_dump_json(indent=2, exclude_none=True), of the retagged document. In doc 2
    # one chunk already carries the new tags and one does not: 1 changed chunk there;
    # doc 3 already carries them everywhere and is not written.
    store = rt.CountingStore()
    d1 = rt.doc("speeches", 1, chunks=1)
    d2 = rt.doc("speeches", 2, chunks=2)
    d2.chunks[0].metadata.factor_tags = list(rt.NEW_FACTOR)
    d3 = rt.doc("speeches", 3, chunks=2, factor=list(rt.NEW_FACTOR))
    for d in (d1, d2, d3):
        rt.put(store, d)
    store.writes.clear()
    counts = retag_documents(store, tier=1, source_id="speeches",
                             factor_tags=list(rt.NEW_FACTOR), entity_tags=[])
    assert counts == {"docs": 3, "changed_docs": 2, "changed_chunks": 2}
    assert sorted(store.writes) == sorted([rt.key(d1), rt.key(d2)])
    expected = d1.model_copy(deep=True)
    expected.chunks[0].metadata.factor_tags = list(rt.NEW_FACTOR)
    assert store.get_text(rt.key(d1)) == expected.model_dump_json(indent=2, exclude_none=True)


@pytest.mark.criterion("C1")
@settings(max_examples=40, deadline=None)
@given(n_chunks=st.lists(st.integers(min_value=1, max_value=4), min_size=1, max_size=4),
       tags=st.lists(st.sampled_from(["inflation", "growth", "labor", "discount_rate",
                                      "rates_vol", "fiscal"]), unique=True, min_size=1),
       ents=st.lists(st.sampled_from(["ai", "china", "semiconductors"]), unique=True))
def test_property_counts_and_content_preserved(rt, n_chunks, tags, ents):
    # oracle: C1 computed in the test. Every document starts with OLD_FACTOR and no
    # entities; a chunk changes unless the target equals that. Everything but the
    # tags round-trips unchanged.
    store = rt.CountingStore()
    docs = [rt.doc("speeches", i + 1, chunks=n) for i, n in enumerate(n_chunks)]
    for d in docs:
        rt.put(store, d)
    same = tags == rt.OLD_FACTOR and ents == []
    counts = retag_documents(store, tier=1, source_id="speeches", factor_tags=list(tags),
                             entity_tags=list(ents))
    assert counts == {"docs": len(docs), "changed_docs": 0 if same else len(docs),
                      "changed_chunks": 0 if same else sum(n_chunks)}
    for d in docs:
        got = rt.load(store, rt.key(d))
        assert all(c.metadata.factor_tags == tags and c.metadata.entity_tags == ents
                   for c in got.chunks)
        assert rt.without_tags(got) == rt.without_tags(d)
