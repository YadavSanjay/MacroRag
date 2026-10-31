"""Fixtures for the synth-route-filters spec tests.

The in-memory pipeline (hashing embedder, in-memory store, identity reranker, NullCache,
disabled tracer) with a real PlanExecuteAgent whose planner and executor run against a
scripted FakeLLM. Corpus: a Federal Reserve note, a BIS note and a market-episode card.
Everything lives under a caller-supplied directory: no repo data dir, no network, no clock.
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

FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
FIXED_DIGEST = "abcdef012345"
FED, BIS, CARD = "Federal Reserve", "Bank for International Settlements", \
    "MacroRAG (derived from FRED)"
# Matches the agent's quantitative trigger ("rise", "rates") as faultline's questions do.
QUERY = "how much did treasury yields rise when rates repriced"
PLANNER_MARK = "You are a planner"

DOCS = {
    "fed_note": (FED, "research_note", ["term_premium"],
                 "Treasury yields rose as the term premium rebuilt over the quarter."),
    "bis_note": (BIS, "research_note", ["term_premium"],
                 "Treasury yields rose across advanced economies as rates repriced."),
    "market_episode_taper_tantrum": (CARD, "market_episode", ["term_premium",
                                                              "cross_asset_scenario"],
                                     "Market episode: Taper tantrum. Treasury yields rose "
                                     "over the following sessions."),
}


def _doc(doc_id: str) -> dict:
    publisher, doc_type, tags, text = DOCS[doc_id]
    meta = {
        "publisher": publisher, "publication": doc_id, "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": f"https://example.invalid/{doc_id}",
        "content_hash": doc_id, "licence": "public_domain_us_gov", "tier": 3, "bucket": "C",
        "doc_type": doc_type, "section": None, "entity_tags": [], "factor_tags": tags,
        "trades_affected": [],
    }
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id, "doc_type": doc_type,
        "publisher": publisher, "publication": doc_id, "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": meta["url"], "content_hash": doc_id,
        "tier": 3, "bucket": "C",
        "chunks": [{"chunk_id": f"{doc_id}::000", "seq": 0, "text": text, "metadata": meta}],
        "timeseries": [],
    }


def responder(system, user):
    """Planner -> a quantitative plan; synthesize -> empty vector; anything else -> answer."""
    if PLANNER_MARK in user:
        return json.dumps({"quantitative": True,
                           "retrieve": [{"id": "r1", "query": "treasury yields rose"}]})
    if "SHOCK VECTOR" in user:
        return json.dumps({"shock_vector": {}})
    return json.dumps({"summary": "narrative", "details": [], "unanswered": []})


def build(root):
    """(pipe, fake_llm) with a real PlanExecuteAgent over the three-document corpus."""
    from agent.agent import PlanExecuteAgent
    from agent.executor import PlanExecutor
    from agent.planner import LLMPlanner
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
    settings.semantic_cache.enabled = False
    settings.escalation.enabled = False
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest=FIXED_DIGEST)
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)
    llm = FakeLLM(responder=responder)
    agent = PlanExecuteAgent(LLMPlanner(llm), PlanExecutor(retriever, None))
    pipe = QueryPipeline(retriever, llm, None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=NullCache(), agent=agent)
    return pipe, llm


def meta_filter(publishers: list[str]):
    from utils.schema import MetaFilter
    return MetaFilter(publishers=list(publishers))


def planner_calls(llm) -> int:
    return sum(1 for _, user in llm.calls if PLANNER_MARK in user)


@pytest.fixture(scope="session")
def srf():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(FED=FED, BIS=BIS, CARD=CARD, QUERY=QUERY, build=build,
                                 meta_filter=meta_filter, planner_calls=planner_calls)
