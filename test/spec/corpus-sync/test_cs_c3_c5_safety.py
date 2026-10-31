"""C3 never deletes, src untouched; C4 dry run; C5 prefix scope and idempotence."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from utils.corpus_sync import sync_objects

_KEYS = st.sampled_from(["tier1/a/1.json", "tier1/a/2.json", "tier1/b/3.json",
                         "tier3/c/4.json", "tier3/c/5.json"])
_BYTES = st.sampled_from([b"", b"x", b"y", b'{"doc": 1}'])
_STORE = st.dictionaries(_KEYS, _BYTES, max_size=5)


@pytest.mark.criterion("C3")
def test_destination_only_keys_kept_and_counted(cs):
    # oracle: C3 — dst holds two keys the source lacks; both keep their bytes, dst_only = 2,
    # nothing is deleted, and the source is never written.
    src = cs.RecordingStore({"tier1/s/a.json": b"a"})
    dst = cs.RecordingStore({"tier1/s/old1.json": b"o1", "tier1/s/old2.json": b"o2"})
    src_before = cs.snapshot(src)
    counts = sync_objects(src, dst, overwrite=True)
    assert dst.get_bytes("tier1/s/old1.json") == b"o1"
    assert dst.get_bytes("tier1/s/old2.json") == b"o2"
    assert dst.deletes == []
    assert src.writes == [] and src.deletes == []
    assert cs.snapshot(src) == src_before
    assert counts == {"copied": 1, "identical": 0, "conflict": 0, "overwritten": 0,
                      "dst_only": 2}


@pytest.mark.criterion("C3")
@settings(max_examples=80, deadline=None)
@given(src=_STORE, dst=_STORE, overwrite=st.booleans())
def test_property_no_deletes_and_dst_keys_preserved(cs, src, dst, overwrite):
    # oracle: C3. Invariant: every dst key survives (no deletes), the source is unchanged,
    # and dst_only = |dst keys - src keys| (set arithmetic).
    s, d = cs.RecordingStore(src), cs.RecordingStore(dst)
    counts = sync_objects(s, d, overwrite=overwrite)
    assert d.deletes == [] and s.writes == [] and s.deletes == []
    assert set(dst) <= set(cs.snapshot(d))
    assert cs.snapshot(s) == src
    assert counts["dst_only"] == len(set(dst) - set(src))


@pytest.mark.criterion("C4")
def test_dry_run_writes_nothing_and_counts_match_real_run(cs):
    # oracle: C4 — one key to copy, one identical, one conflicting, one dst-only. The dry run
    # writes nothing; the real run that follows returns the same counts.
    src = cs.RecordingStore({"k/new.json": b"n", "k/same.json": b"s", "k/diff.json": b"1"})
    dst = cs.RecordingStore({"k/same.json": b"s", "k/diff.json": b"2", "k/only.json": b"o"})
    before = cs.snapshot(dst)
    dry = sync_objects(src, dst, dry_run=True)
    assert dst.writes == [] and dst.deletes == []
    assert cs.snapshot(dst) == before
    assert dry == {"copied": 1, "identical": 1, "conflict": 1, "overwritten": 0,
                   "dst_only": 1}
    assert sync_objects(src, dst) == dry


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@settings(max_examples=80, deadline=None)
@given(src=_STORE, dst=_STORE, overwrite=st.booleans())
def test_property_dry_run_predicts_real_run(cs, src, dst, overwrite):
    # oracle: C4. Relation: dry-run counts equal the counts of the real run on the same
    # stores, and the dry run leaves dst unchanged with no writes. Anchor: the counts carry
    # the contract's keys, and after the real run every source key is in dst (C1).
    s, d = cs.RecordingStore(src), cs.RecordingStore(dst)
    dry = sync_objects(s, d, dry_run=True, overwrite=overwrite)
    assert set(dry) == cs.COUNT_KEYS
    assert d.writes == [] and cs.snapshot(d) == dst
    assert sync_objects(s, d, overwrite=overwrite) == dry
    assert set(src) <= set(cs.snapshot(d))


@pytest.mark.criterion("C5")
def test_prefix_limits_keys_considered_and_counted(cs):
    # oracle: C5 — prefix "tier3/": only tier3/c/4.json is copied; tier1/a/1.json (outside)
    # is not written; dst_only counts tier3/c/9.json but not tier1/z/0.json.
    src = cs.RecordingStore({"tier1/a/1.json": b"a", "tier3/c/4.json": b"c"})
    dst = cs.RecordingStore({"tier3/c/9.json": b"9", "tier1/z/0.json": b"0"})
    counts = sync_objects(src, dst, prefix="tier3/")
    assert dst.writes == ["tier3/c/4.json"]
    assert dst.get_bytes("tier1/a/1.json") is None
    assert counts == {"copied": 1, "identical": 0, "conflict": 0, "overwritten": 0,
                      "dst_only": 1}


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@settings(max_examples=80, deadline=None)
@given(src=_STORE, dst=_STORE, prefix=st.sampled_from(["", "tier1/", "tier1/a/", "tier3/"]),
       overwrite=st.booleans())
def test_property_second_sync_changes_nothing(cs, src, dst, prefix, overwrite):
    # oracle: C5. Relation: syncing twice, the second run copies and overwrites nothing and
    # writes nothing; with overwrite it also leaves no conflict. Keys outside the prefix are
    # never written.
    s, d = cs.RecordingStore(src), cs.RecordingStore(dst)
    sync_objects(s, d, prefix=prefix, overwrite=overwrite)
    assert all(k.startswith(prefix) for k in d.writes)
    d.writes.clear()
    second = sync_objects(s, d, prefix=prefix, overwrite=overwrite)
    assert d.writes == []
    assert second["copied"] == 0 and second["overwritten"] == 0
    if overwrite:
        assert second["conflict"] == 0
