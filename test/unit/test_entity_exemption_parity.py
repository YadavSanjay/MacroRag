"""The entity-facet exemption (MetaFilter.entity_exempt_factor_tags) admits the same
chunks server-side in Qdrant as the pure-python predicate does in the in-memory
store — the spec tests only exercise the in-memory path."""

from __future__ import annotations

import pytest

from utils.schema import MetaFilter

pytest.importorskip("qdrant_client")

_FILTERS = [
    MetaFilter(entity_tags=["semiconductors"]),
    MetaFilter(entity_tags=["semiconductors"], entity_exempt_factor_tags=["equity_vol"]),
    MetaFilter(entity_tags=["semiconductors"], entity_exempt_factor_tags=["equity_vol"],
               date_to="2026-03-01"),
    MetaFilter(entity_tags=["semiconductors"], entity_exempt_factor_tags=["no_such_tag"]),
]


@pytest.fixture
def stores(corpus_dir):
    from embeddings.hashing import HashingEmbedder
    from utils.config import QdrantSettings
    from utils.corpus import iter_chunk_points
    from vectorstores.memory import InMemoryVectorStore
    from vectorstores.qdrant_store import QdrantStore

    emb = HashingEmbedder(dense_dim=64)
    pts = list(iter_chunk_points(corpus_dir))
    vecs = emb.embed_documents([p.embed_text for p in pts])
    qdrant = QdrantStore(QdrantSettings(location=":memory:", collection="t"), dense_dim=64)
    qdrant.ensure_collection(recreate=True)
    qdrant.upsert(pts, vecs)
    qdrant.finalize_index()
    memory = InMemoryVectorStore()
    memory.upsert(pts, vecs)
    return emb, qdrant, memory


@pytest.mark.parametrize("filt", _FILTERS)
def test_qdrant_and_memory_agree_on_exemption(stores, filt):
    emb, qdrant, memory = stores
    q = emb.embed_query("semiconductor export controls and equity volatility stress")
    got_q = {h.chunk_id for h in qdrant.search(q, limit=50, filt=filt)}
    got_m = {h.chunk_id for h in memory.search(q, limit=50, filt=filt)}
    assert got_q == got_m


def test_exemption_admits_the_exempt_chunks(stores):
    emb, qdrant, _ = stores
    q = emb.embed_query("semiconductor export controls and equity volatility stress")
    hard = {h.chunk_id for h in qdrant.search(q, limit=50, filt=_FILTERS[0])}
    soft = {h.chunk_id for h in qdrant.search(q, limit=50, filt=_FILTERS[1])}
    assert hard == {"bis_rule::000"}
    assert soft == {"bis_rule::000", "dfast_scen::000", "dfast_scen::001"}
