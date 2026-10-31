"""Hashing embedder + in-memory hybrid store + timeseries store."""

from __future__ import annotations

from embeddings.hashing import HashingEmbedder
from utils.schema import MetaFilter
from vectorstores.timeseries_store import TimeseriesStore


def test_hashing_embedder_is_deterministic():
    e = HashingEmbedder(dense_dim=32)
    a = e.embed_query("federal funds rate")
    b = e.embed_query("federal funds rate")
    assert a.dense == b.dense
    assert len(a.dense) == 32
    assert a.sparse.indices == b.sparse.indices


def test_hashing_sparse_overlap_reflects_shared_tokens():
    e = HashingEmbedder()
    x = e.embed_query("unemployment rate stress")
    y = e.embed_query("unemployment rate scenario")
    z = e.embed_query("semiconductor export tariff")
    shared_xy = set(x.sparse.indices) & set(y.sparse.indices)
    shared_xz = set(x.sparse.indices) & set(z.sparse.indices)
    assert len(shared_xy) > len(shared_xz)


def test_memory_store_hybrid_search_ranks_relevant_first(indexed):
    embedder, store, _ = indexed
    q = embedder.embed_query("severely adverse unemployment stress test scenario")
    hits = store.search(q, limit=3)
    assert hits and hits[0].payload["source_id"] == "dfast"


def test_memory_store_facet_filter(indexed):
    embedder, store, _ = indexed
    q = embedder.embed_query("China semiconductors export controls")
    hits = store.search(q, limit=5, filt=MetaFilter(entity_tags=["china"]))
    assert hits and all("china" in h.payload.get("entity_tags", []) for h in hits)


def test_memory_store_get_by_chunk_ids(indexed):
    _, store, _ = indexed
    got = store.get_by_chunk_ids(["fomc_stmt::000", "missing::000"])
    assert len(got) == 1 and got[0].chunk_id == "fomc_stmt::000"


def test_timeseries_store_build_and_query(tmp_path, corpus_dir):
    from utils.corpus import iter_timeseries_rows
    db = tmp_path / "ts.sqlite"
    ts = TimeseriesStore(db)
    n = ts.build(iter_timeseries_rows(corpus_dir))
    assert n == 4
    rows = ts.query(source="DFAST_2026", variable="unemployment_rate", scenario="severely_adverse")
    assert len(rows) == 4
    assert {r["value"] for r in rows} == {5.9, 7.2, 8.2, 9.0}
    assert "DFAST_2026" in ts.sources()
