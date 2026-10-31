"""Fixtures for the cache-scope spec tests.

The in-memory pipeline (hashing embedder, so identical text embeds identically; in-memory
store; identity reranker; scripted FakeLLM; disabled tracer) over a Federal Reserve note and a
BIS note, with injectable L1 and L2 caches and a manifest whose corpus digest the test sets.
No network, no ``data/``, no clock.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

FED, BIS = "Federal Reserve", "Bank for International Settlements"
QUERY = "how did treasury yields behave"     # no quantitative trigger word: no agent
FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
DOCS = {"fed_note": (FED, "Treasury yields fell as markets priced cuts."),
        "bis_note": (BIS, "Treasury yields fell across advanced economies.")}


def _doc(doc_id: str) -> dict:
    publisher, text = DOCS[doc_id]
    meta = {
        "publisher": publisher, "publication": doc_id, "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": f"https://example.invalid/{doc_id}",
        "content_hash": doc_id, "licence": "public_domain_us_gov", "tier": 3, "bucket": "C",
        "doc_type": "research_note", "section": None, "entity_tags": [],
        "factor_tags": ["cross_asset_scenario"], "trades_affected": [],
    }
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id, "doc_type": "research_note",
        "publisher": publisher, "publication": doc_id, "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": meta["url"], "content_hash": doc_id,
        "tier": 3, "bucket": "C",
        "chunks": [{"chunk_id": f"{doc_id}::000", "seq": 0, "text": text, "metadata": meta}],
        "timeseries": [],
    }


def responder(system, user):
    if "SHOCK VECTOR" in user:
        return json.dumps({"shock_vector": {}})
    return json.dumps({"summary": "narrative", "details": [], "unanswered": []})


def build(root, *, l1=None, l2=None, digest: str = "abcdef012345"):
    """A pipeline over the two notes; L1 defaults to NullCache, L2 to none."""
    from embeddings.hashing import HashingEmbedder
    from llm.fake import FakeLLM
    from pipelines.query_pipeline import QueryPipeline
    from retriever.rerank import IdentityReranker
    from retriever.retriever import Retriever
    from utils.cache import NullCache
    from utils.config import RagSettings, RetrievalSettings
    from utils.corpus import iter_chunk_points
    from utils.manifest import build_manifest, write_manifest
    from utils.trace import Tracer
    from vectorstores.memory import InMemoryVectorStore

    root = Path(root)
    corpus = root / "processed"
    (corpus / "tierx").mkdir(parents=True, exist_ok=True)
    for doc_id in DOCS:
        (corpus / "tierx" / f"{doc_id}.json").write_text(json.dumps(_doc(doc_id)),
                                                         encoding="utf-8")
    embedder = HashingEmbedder(dense_dim=64)
    store = InMemoryVectorStore()
    points = list(iter_chunk_points(corpus))
    store.upsert(points, embedder.embed_documents([p.embed_text for p in points]))
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=10))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    settings.retrieval.hydrate_timeseries = False
    settings.escalation.enabled = False
    settings.semantic_cache.enabled = False
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest=digest)
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)
    return QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=l1 if l1 is not None else NullCache(), semantic_cache=l2)


def mem_l2():
    from utils.cache import InMemorySemanticCache
    return InMemorySemanticCache(threshold=0.97)


def redis_l2():
    from utils.cache import RedisSemanticCache
    from utils.fakeredis import FakeRedis
    return RedisSemanticCache(FakeRedis(), threshold=0.97)


def mem_l1():
    from utils.cache import InMemoryCache
    return InMemoryCache()


def pubs_filter(publishers):
    from utils.schema import MetaFilter
    return MetaFilter(publishers=list(publishers))


def publishers(result) -> set:
    return {c.payload.get("publisher") for c in result.chunks}


@pytest.fixture(scope="session")
def cs():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(FED=FED, BIS=BIS, QUERY=QUERY, build=build, mem_l2=mem_l2,
                                 redis_l2=redis_l2, mem_l1=mem_l1, pubs_filter=pubs_filter,
                                 publishers=publishers)
