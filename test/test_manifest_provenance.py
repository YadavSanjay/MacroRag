"""Corpus-state provenance (D1): a content-addressed world_view_id and the
append-only build history that makes it auditable. All local — no network."""

from __future__ import annotations

from utils.config import RagSettings
from utils.manifest import (
    append_manifest_history,
    build_manifest,
    corpus_digest,
    diff_manifests,
    find_build,
    history_target,
    read_manifest,
    read_manifest_history,
    world_view_id_from,
    write_manifest,
)
from utils.objectstore import LocalObjectStore


def _target(tmp_path):
    return LocalObjectStore(tmp_path), "manifest.json"


# ── content addressing ───────────────────────────────────────────────────────

def test_digest_is_stable_and_order_independent():
    a = corpus_digest({"c1": "h1", "c2": "h2"})
    b = corpus_digest({"c2": "h2", "c1": "h1"})          # different insertion order
    c = corpus_digest([("c2", "h2"), ("c1", "h1")])      # pairs, not a mapping
    assert a == b == c and len(a) == 12


def test_digest_changes_when_content_changes():
    base = corpus_digest({"c1": "h1", "c2": "h2"})
    assert corpus_digest({"c1": "h1", "c2": "CHANGED"}) != base   # edited chunk
    assert corpus_digest({"c1": "h1"}) != base                    # departed chunk
    assert corpus_digest({"c1": "h1", "c2": "h2", "c3": "h3"}) != base   # added


def test_digest_is_not_confused_by_field_boundaries():
    # naive concatenation would collide these two corpora
    assert corpus_digest({"a": "bc"}) != corpus_digest({"ab": "c"})


def test_digest_of_empty_corpus_is_defined():
    assert len(corpus_digest({})) == 12


# ── world_view_id ────────────────────────────────────────────────────────────

def test_world_view_id_carries_the_digest():
    wv = world_view_id_from({"built_at": "2026-08-11T10:00:00+00:00",
                             "corpus_digest": "abc123def456"})
    assert wv == "macro-2026-08-11-abc123def456"


def test_same_day_different_corpora_no_longer_collide():
    day = "2026-08-11T10:00:00+00:00"
    a = world_view_id_from({"built_at": day, "corpus_digest": corpus_digest({"c": "1"})})
    b = world_view_id_from({"built_at": day, "corpus_digest": corpus_digest({"c": "2"})})
    assert a != b                        # the bug D1 exists to fix
    assert a.startswith("macro-2026-08-11-")   # date prefix still parseable


def test_legacy_manifest_keeps_date_only_form():
    # pre-digest manifests must not be given a false identity retroactively
    assert world_view_id_from({"built_at": "2026-01-02T00:00:00+00:00"}) \
        == "macro-2026-01-02"


def test_world_view_id_none_without_manifest_or_date():
    assert world_view_id_from(None) is None
    assert world_view_id_from({}) is None


def test_build_manifest_records_digest():
    man = build_manifest(RagSettings(), chunks=3, observations=4, digest="d1d2d3")
    assert man["corpus_digest"] == "d1d2d3" and man["chunks"] == 3
    assert build_manifest(RagSettings())["corpus_digest"] is None


# ── history log ──────────────────────────────────────────────────────────────

def test_history_target_sits_beside_the_manifest(tmp_path):
    _, key = history_target(_target(tmp_path))
    assert key == "manifest_history.jsonl"


def test_history_is_append_only_and_newest_first(tmp_path):
    target = _target(tmp_path)
    for i, digest in enumerate(("d1", "d2", "d3")):
        append_manifest_history(target, build_manifest(RagSettings(), chunks=i,
                                                       digest=digest))
    hist = read_manifest_history(target)
    assert [h["corpus_digest"] for h in hist] == ["d3", "d2", "d1"]
    assert hist[0]["world_view_id"].endswith("-d3")


def test_history_trims_to_max_entries(tmp_path):
    target = _target(tmp_path)
    for i in range(6):
        append_manifest_history(target, build_manifest(RagSettings(), digest=f"d{i}"),
                                max_entries=3)
    hist = read_manifest_history(target)
    assert len(hist) == 3
    assert [h["corpus_digest"] for h in hist] == ["d5", "d4", "d3"]


def test_history_limit_returns_most_recent(tmp_path):
    target = _target(tmp_path)
    for d in ("d1", "d2", "d3"):
        append_manifest_history(target, build_manifest(RagSettings(), digest=d))
    assert [h["corpus_digest"] for h in read_manifest_history(target, limit=2)] \
        == ["d3", "d2"]


def test_history_empty_when_never_written(tmp_path):
    assert read_manifest_history(_target(tmp_path)) == []


def test_history_skips_corrupt_lines(tmp_path):
    store, _ = _target(tmp_path)
    store.put_text("manifest_history.jsonl",
                   '{"corpus_digest": "ok"}\nnot json\n\n')
    hist = read_manifest_history(_target(tmp_path))
    assert [h["corpus_digest"] for h in hist] == ["ok"]


def test_history_write_failure_never_raises(tmp_path):
    class Exploding(LocalObjectStore):
        def put_bytes(self, key, data):
            raise OSError("disk full")

    # best-effort by design: an audit log must not fail the build it records
    append_manifest_history((Exploding(tmp_path), "manifest.json"),
                            build_manifest(RagSettings(), digest="d1"))


# ── audit lookup + diff ──────────────────────────────────────────────────────

def test_find_build_locates_a_past_corpus_state(tmp_path):
    target = _target(tmp_path)
    man = build_manifest(RagSettings(), chunks=7, digest="deadbeef1234")
    append_manifest_history(target, man)
    found = find_build(target, world_view_id_from(man))
    assert found is not None and found["chunks"] == 7


def test_find_build_returns_none_for_unknown_state(tmp_path):
    target = _target(tmp_path)
    append_manifest_history(target, build_manifest(RagSettings(), digest="d1"))
    assert find_build(target, "macro-1999-01-01-nope") is None


def test_diff_reports_corpus_and_count_changes():
    before = build_manifest(RagSettings(), chunks=10, observations=5, digest="d1")
    after = build_manifest(RagSettings(), chunks=12, observations=5, digest="d2")
    fields = {c["field"] for c in diff_manifests(before, after)}
    assert fields == {"corpus_digest", "chunks"}


def test_diff_of_identical_builds_is_empty():
    man = build_manifest(RagSettings(), chunks=1, digest="d1")
    assert diff_manifests(man, dict(man)) == []


def test_diff_reports_embedding_block_as_one_unit():
    before = build_manifest(RagSettings(), digest="d1")
    after = dict(before, embedding={**before["embedding"], "dense_dim": 512})
    changes = diff_manifests(before, after)
    assert [c["field"] for c in changes] == ["embedding"]
    assert changes[0]["after"]["dense_dim"] == 512


# ── round trip through the manifest object store ─────────────────────────────

def test_manifest_round_trip_preserves_digest(tmp_path):
    target = _target(tmp_path)
    write_manifest(target, build_manifest(RagSettings(), chunks=2, digest="abc123"))
    assert read_manifest(target)["corpus_digest"] == "abc123"
