"""Integration test for the real Qdrant adapter (in-memory mode, hashing
embedder — no model download). Validates named dense+sparse vectors, server-side
RRF fusion, facet + datetime filters, and chunk-id lookup."""

from __future__ import annotations

import pytest

from utils.schema import MetaFilter

pytest.importorskip("qdrant_client")


@pytest.fixture
def qdrant_store(corpus_dir):
    from embeddings.hashing import HashingEmbedder
    from utils.config import QdrantSettings
    from utils.corpus import iter_chunk_points
    from vectorstores.qdrant_store import QdrantStore

    emb = HashingEmbedder(dense_dim=64)
    store = QdrantStore(QdrantSettings(location=":memory:", collection="t"), dense_dim=64)
    store.ensure_collection(recreate=True)
    pts = list(iter_chunk_points(corpus_dir))
    store.upsert(pts, emb.embed_documents([p.embed_text for p in pts]))
    store.finalize_index()
    return emb, store, pts


def test_qdrant_hybrid_search(qdrant_store):
    emb, store, pts = qdrant_store
    assert store.count() == len(pts)
    q = emb.embed_query("severely adverse unemployment stress test scenario")
    hits = store.search(q, limit=3)
    assert hits and hits[0].payload["source_id"] == "dfast"
    assert hits[0].text  # payload text round-trips


def test_qdrant_facet_filter(qdrant_store):
    emb, store, _ = qdrant_store
    q = emb.embed_query("China semiconductors export controls")
    hits = store.search(q, limit=5, filt=MetaFilter(entity_tags=["china"]))
    assert hits and all("china" in h.payload.get("entity_tags", []) for h in hits)


def test_qdrant_datetime_filter(qdrant_store):
    emb, store, _ = qdrant_store
    q = emb.embed_query("federal funds rate")
    hits = store.search(q, limit=5, filt=MetaFilter(date_from="2026-06-01",
                                                    date_to="2026-06-30"))
    assert hits and all(h.payload["release_date"].startswith("2026-06") for h in hits)


def test_qdrant_get_by_chunk_ids(qdrant_store):
    _, store, pts = qdrant_store
    got = store.get_by_chunk_ids([pts[0].chunk_id])
    assert got and got[0].chunk_id == pts[0].chunk_id


def test_qdrant_int8_quantization_and_tenant_filter(corpus_dir):
    from embeddings.hashing import HashingEmbedder
    from utils.config import QdrantSettings
    from utils.corpus import iter_chunk_points
    from vectorstores.qdrant_store import QdrantStore

    emb = HashingEmbedder(dense_dim=64)
    store = QdrantStore(QdrantSettings(location=":memory:", collection="q",
                                       quantization=True), dense_dim=64)
    store.ensure_collection(recreate=True)        # int8 config must not error
    pts = list(iter_chunk_points(corpus_dir))
    store.upsert(pts, emb.embed_documents([p.embed_text for p in pts]))
    q = emb.embed_query("severely adverse unemployment")
    # public corpus has no tenant_id, so a tenant filter still sees it
    hits = store.search(q, limit=3, filt=MetaFilter(tenant_id="tenant-a"))
    assert hits and hits[0].payload["source_id"] == "dfast"
