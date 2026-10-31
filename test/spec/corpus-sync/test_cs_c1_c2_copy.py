"""C1 copy missing / skip identical; C2 conflict vs overwrite."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from utils.corpus_sync import sync_objects

_KEYS = st.sampled_from(["tier1/a/1.json", "tier1/a/2.json", "tier1/b/3.json",
                         "tier3/c/4.json", "tier3/c/5.json"])
_BYTES = st.sampled_from([b"", b"x", b"y", b'{"doc": 1}', "café".encode()])
_STORE = st.dictionaries(_KEYS, _BYTES, max_size=5)


def _model(src: dict, dst: dict, overwrite: bool) -> tuple[dict, dict]:
    """Hand model of C1-C3: (expected dst contents, expected counts)."""
    out, counts = dict(dst), dict.fromkeys(
        ["copied", "identical", "conflict", "overwritten", "dst_only"], 0)
    for k, v in src.items():
        if k not in dst:
            out[k] = v
            counts["copied"] += 1
        elif dst[k] == v:
            counts["identical"] += 1
        elif overwrite:
            out[k] = v
            counts["overwritten"] += 1
        else:
            counts["conflict"] += 1
    counts["dst_only"] = len(set(dst) - set(src))
    return out, counts


@pytest.mark.criterion("C1")
def test_missing_keys_copied_byte_exact_identical_skipped(cs):
    # oracle: C1 — a.json is absent from dst (copied, same bytes); b.json has equal bytes in
    # both (identical, not written). Non-ASCII bytes check the copy is byte for byte.
    payload = "{\"t\": \"café\"}".encode()
    src = cs.RecordingStore({"tier1/s/a.json": payload, "tier1/s/b.json": b"same"})
    dst = cs.RecordingStore({"tier1/s/b.json": b"same"})
    counts = sync_objects(src, dst)
    assert dst.get_bytes("tier1/s/a.json") == payload
    assert dst.get_bytes("tier1/s/b.json") == b"same"
    assert dst.writes == ["tier1/s/a.json"]
    assert counts == {"copied": 1, "identical": 1, "conflict": 0, "overwritten": 0,
                      "dst_only": 0}


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None)
@given(src=_STORE)
def test_property_empty_destination_receives_exact_copy(cs, src):
    # oracle: C1. Into an empty dst every source key is copied: dst equals src and
    # copied = len(src).
    s, d = cs.RecordingStore(src), cs.RecordingStore()
    counts = sync_objects(s, d)
    assert cs.snapshot(d) == src
    assert counts["copied"] == len(src)
    assert set(counts) == cs.COUNT_KEYS


@pytest.mark.criterion("C2")
def test_conflict_kept_without_overwrite_replaced_with_it(cs):
    # oracle: C2 — tier1/s/x.json differs ("new" vs "old"). Default: dst keeps "old",
    # conflict = 1, nothing written. overwrite=True: dst gets "new", overwritten = 1.
    src = cs.RecordingStore({"tier1/s/x.json": b"new"})
    dst = cs.RecordingStore({"tier1/s/x.json": b"old"})
    kept = sync_objects(src, dst)
    assert dst.get_bytes("tier1/s/x.json") == b"old"
    assert dst.writes == []
    assert kept == {"copied": 0, "identical": 0, "conflict": 1, "overwritten": 0,
                    "dst_only": 0}
    replaced = sync_objects(src, dst, overwrite=True)
    assert dst.get_bytes("tier1/s/x.json") == b"new"
    assert dst.writes == ["tier1/s/x.json"]
    assert replaced == {"copied": 0, "identical": 0, "conflict": 0, "overwritten": 1,
                        "dst_only": 0}


@pytest.mark.criterion("C2")
@settings(max_examples=120, deadline=None)
@given(src=_STORE, dst=_STORE, overwrite=st.booleans())
def test_property_matches_hand_model(cs, src, dst, overwrite):
    # oracle: C1/C2/C3 hand model `_model` above; the four per-key counts sum to the
    # number of source keys (arithmetic identity in C2).
    s, d = cs.RecordingStore(src), cs.RecordingStore(dst)
    counts = sync_objects(s, d, overwrite=overwrite)
    want_dst, want_counts = _model(src, dst, overwrite)
    assert cs.snapshot(d) == want_dst
    assert counts == want_counts
    assert (counts["copied"] + counts["identical"] + counts["conflict"]
            + counts["overwritten"]) == len(src)
