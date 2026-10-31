"""Incremental indexing: embed/upsert only new+changed chunks, delete departed
ones — no full re-embed. All fakes (hashing embedder + in-memory store)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from embeddings.hashing import HashingEmbedder
from pipelines.index_pipeline import _corpus_digest, index_chunks_incremental
from vectorstores.memory import InMemoryVectorStore


def _settings(corpus_dir):
    return SimpleNamespace(processed_path=corpus_dir,
                           embedding=SimpleNamespace(use_grounding_prefix=True))


def _write_doc(corpus_dir, doc_id, content_hash, text):
    doc = {
        "doc_id": doc_id, "source_id": "s", "title": "t", "doc_type": "d",
        "publisher": "p", "publication": "pub", "release_date": "2026-06-01",
        "retrieval_date": "2026-06-19", "url": f"https://x/{doc_id}",
        "content_hash": content_hash, "tier": 1, "bucket": "B",
        "chunks": [{
            "chunk_id": f"{doc_id}::000", "seq": 0, "text": text,
            "metadata": {"content_hash": content_hash, "tier": 1, "bucket": "B",
                         "publisher": "p", "publication": "pub",
                         "release_date": "2026-06-01"},
        }],
        "timeseries": [],
    }
    (corpus_dir / "tier1" / f"{doc_id}.json").write_text(json.dumps(doc),
                                                         encoding="utf-8")


def test_first_run_indexes_everything(corpus_dir):
    store = InMemoryVectorStore()
    r = index_chunks_incremental(HashingEmbedder(dense_dim=64), store, _settings(corpus_dir))
    assert r["scanned"] > 0
    assert r["new"] == r["scanned"] and r["changed"] == 0 and r["unchanged"] == 0
    assert r["indexed"] == r["scanned"] == store.count()
    assert r["deleted"] == 0


def test_second_run_is_a_noop(corpus_dir):
    emb, store = HashingEmbedder(dense_dim=64), InMemoryVectorStore()
    index_chunks_incremental(emb, store, _settings(corpus_dir))
    r = index_chunks_incremental(emb, store, _settings(corpus_dir))
    assert r["unchanged"] == r["scanned"] and r["new"] == 0 and r["changed"] == 0
    assert r["indexed"] == 0 and r["deleted"] == 0


def test_new_changed_and_stale(corpus_dir):
    emb, store = HashingEmbedder(dense_dim=64), InMemoryVectorStore()
    index_chunks_incremental(emb, store, _settings(corpus_dir))
    base = store.count()

    # changed: re-write fomc_stmt with a new content_hash; new: add a doc; stale: drop bis_rule
    _write_doc(corpus_dir, "fomc_stmt", "fomc_stmt_v2", "updated FOMC text")
    _write_doc(corpus_dir, "newdoc", "newdoc_h1", "a brand new chunk")
    (corpus_dir / "tier1" / "bis_rule.json").unlink()

    r = index_chunks_incremental(emb, store, _settings(corpus_dir))
    assert r["new"] == 1 and r["changed"] == 1 and r["stale"] == 1
    assert r["indexed"] == 2 and r["deleted"] == 1
    # net: +1 new, fomc_stmt re-upserted (same id), -1 deleted => count unchanged
    assert store.count() == base
    sigs = store.existing_signatures()
    assert "bis_rule::000" not in sigs                 # departed chunk removed
    assert sigs["fomc_stmt::000"] == "fomc_stmt_v2"    # changed chunk re-signed
    assert "newdoc::000" in sigs                       # new chunk added


def test_dry_run_writes_nothing(corpus_dir):
    emb, store = HashingEmbedder(dense_dim=64), InMemoryVectorStore()
    index_chunks_incremental(emb, store, _settings(corpus_dir))
    _write_doc(corpus_dir, "newdoc", "newdoc_h1", "a brand new chunk")
    before = store.count()
    r = index_chunks_incremental(None, store, _settings(corpus_dir), dry_run=True)
    assert r["dry_run"] is True and r["new"] == 1 and r["indexed"] == 0
    assert store.count() == before                     # nothing written


# ── corpus digest (D1): the identity behind world_view_id ────────────────────

def test_digest_tracks_real_indexed_content(corpus_dir):
    """The property that matters end-to-end: identical corpus -> identical id,
    changed corpus -> different id. Driven through the real store, not a stub."""
    emb, store = HashingEmbedder(dense_dim=64), InMemoryVectorStore()
    index_chunks_incremental(emb, store, _settings(corpus_dir))
    first = _corpus_digest(store)
    assert first and len(first) == 12

    index_chunks_incremental(emb, store, _settings(corpus_dir))   # no-op re-index
    assert _corpus_digest(store) == first        # unchanged corpus keeps its id

    _write_doc(corpus_dir, "fomc_stmt", "fomc_stmt_v2", "updated FOMC text")
    index_chunks_incremental(emb, store, _settings(corpus_dir))
    changed = _corpus_digest(store)
    assert changed != first                      # edited chunk -> new id

    _write_doc(corpus_dir, "newdoc", "newdoc_h1", "a brand new chunk")
    index_chunks_incremental(emb, store, _settings(corpus_dir))
    assert _corpus_digest(store) not in (first, changed)   # added chunk -> new id


def test_digest_degrades_to_none_when_store_cannot_enumerate():
    class Unsupported(InMemoryVectorStore):
        def existing_signatures(self):
            raise NotImplementedError("no signature enumeration")

    # a missing digest costs the legacy date-only id; it must not fail the build
    assert _corpus_digest(Unsupported()) is None
