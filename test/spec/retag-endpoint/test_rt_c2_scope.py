"""C2: dry run, idempotence, and nothing outside the prefix is touched."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ingest.common.retag import retag_documents


def _seed(rt):
    store = rt.CountingStore()
    target = [rt.doc("speeches", 1), rt.doc("speeches", 2)]
    other_source = rt.doc("press_monetary", 3)
    other_tier = rt.doc("speeches", 4, tier=2)
    for d in target + [other_source, other_tier]:
        rt.put(store, d)
    store.put_text("tier1/speeches/notes.txt", "not a document")
    store.writes.clear()
    return store, target, other_source, other_tier


@pytest.mark.criterion("C2")
def test_dry_run_counts_and_writes_nothing(rt):
    # oracle: C2 — dry_run returns the same counts as a real run (2 docs, 2 changed,
    # 4 chunks: two 2-chunk speeches docs) and writes nothing.
    store, target, _, _ = _seed(rt)
    before = {k: store.get_text(k) for k in store.iter_keys("")}
    counts = retag_documents(store, tier=1, source_id="speeches",
                             factor_tags=list(rt.NEW_FACTOR), entity_tags=[], dry_run=True)
    assert counts == {"docs": 2, "changed_docs": 2, "changed_chunks": 4}
    assert store.writes == []
    assert {k: store.get_text(k) for k in store.iter_keys("")} == before


@pytest.mark.criterion("C2")
def test_second_run_is_a_no_op_and_other_keys_untouched(rt):
    # oracle: C2 — after one run, a second with the same tags reports zero changes and
    # writes nothing; the press_monetary doc, the tier-2 speeches doc and the .txt key
    # keep their exact text throughout.
    store, target, other_source, other_tier = _seed(rt)
    untouched = {k: store.get_text(k) for k in
                 (rt.key(other_source), rt.key(other_tier), "tier1/speeches/notes.txt")}
    first = retag_documents(store, tier=1, source_id="speeches",
                            factor_tags=list(rt.NEW_FACTOR), entity_tags=[])
    assert first == {"docs": 2, "changed_docs": 2, "changed_chunks": 4}
    store.writes.clear()
    second = retag_documents(store, tier=1, source_id="speeches",
                             factor_tags=list(rt.NEW_FACTOR), entity_tags=[])
    assert second == {"docs": 2, "changed_docs": 0, "changed_chunks": 0}
    assert store.writes == []
    assert {k: store.get_text(k) for k in untouched} == untouched


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=25, deadline=None)
@given(tags=st.lists(st.sampled_from(["inflation", "growth", "labor", "fiscal"]),
                     unique=True, min_size=1))
def test_property_dry_run_then_real_run_agree(rt, tags):
    # oracle: C2. Relation: a dry run's counts equal the real run's counts that follow,
    # and only the real run writes, and only target keys.
    store, target, _, _ = _seed(rt)
    dry = retag_documents(store, tier=1, source_id="speeches", factor_tags=list(tags),
                          entity_tags=[], dry_run=True)
    assert store.writes == []
    real = retag_documents(store, tier=1, source_id="speeches", factor_tags=list(tags),
                           entity_tags=[])
    assert dry == real == {"docs": 2, "changed_docs": 2, "changed_chunks": 4}
    assert sorted(store.writes) == sorted(rt.key(d) for d in target)
