"""Fixtures for the synth-vector-report spec tests.

C1/C2 use hand-built drop records, ``ShockField``s and ``RetrievedChunk``s. C3 builds the
in-memory pipeline (hashing embedder, in-memory store, identity reranker, scripted
FakeLLM, NullCache, disabled tracer) over two market-episode documents and one research
note, whose texts hold no number-and-unit quantity. Everything lives under a
caller-supplied directory: no repo data dir, no network, no clock.
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

CODE = "episode_mixed"
FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
FIXED_DIGEST = "abcdef012345"
QUERY = "how would markets move under this shock"
CARD_FIELDS = {"VIX", "UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "IG_OAS", "HY_OAS",
               "USDTWD", "USDJPY"}

TARIFF, DEEPSEEK, NOTE = "market_episode_tariff", "market_episode_deepseek", "credit_note"
DOCS = {
    TARIFF: ("market_episode", "Market episode: tariff shock. Long yields rose and the yen "
                               "rallied in the following sessions."),
    DEEPSEEK: ("market_episode", "Market episode: AI selloff. Credit spreads widened "
                                 "slightly in the following sessions."),
    NOTE: ("research_note", "High-yield spreads widened as risk appetite faded."),
}


def cid(doc_id: str) -> str:
    return f"{doc_id}::001" if doc_id.startswith("market_episode") else f"{doc_id}::000"


def chunk(doc_id: str):
    from utils.schema import RetrievedChunk
    doc_type, text = DOCS[doc_id]
    payload = {"release_date": "2026-02-05", "publisher": "X", "publication": "Y",
               "doc_id": doc_id, "doc_type": doc_type}
    return RetrievedChunk(cid(doc_id), text, 1.0, payload)


def sf(name: str, value: float, unit: str, docs: list[str], *,
       validation: list[str] | None = None):
    from synthesis.schema import ShockField
    return ShockField(name, value, unit, chunk_ids=[cid(d) for d in docs], rationale="",
                      grounding_mode="analog", validation=list(validation or []))


def warns(diag) -> list[dict]:
    return [w for w in diag.validation if w.get("code") == CODE]


def _doc(doc_id: str) -> dict:
    doc_type, text = DOCS[doc_id]
    meta = {
        "publisher": "MacroRAG", "publication": doc_id, "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": f"https://example.invalid/{doc_id}",
        "content_hash": doc_id, "licence": "derived_fred", "tier": 3, "bucket": "C",
        "doc_type": doc_type, "section": None, "entity_tags": [],
        "factor_tags": ["cross_asset_scenario"], "trades_affected": [],
    }
    seq = 1 if doc_type == "market_episode" else 0
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id, "doc_type": doc_type,
        "publisher": "MacroRAG", "publication": doc_id, "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": meta["url"], "content_hash": doc_id,
        "tier": 3, "bucket": "C",
        "chunks": [{"chunk_id": cid(doc_id), "seq": seq, "text": text, "metadata": meta}],
        "timeseries": [],
    }


def responder_for(shock_vector: dict):
    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": shock_vector})
        return json.dumps({"summary": "scenario narrative", "details": [],
                           "unanswered": []})
    return responder


def emitted(name: str, value: float, unit: str, doc_id: str) -> dict:
    return {name: {"value": value, "unit": unit, "chunk_ids": [cid(doc_id)],
                   "grounding_mode": "analog", "rationale": "episode move"}}


def run_pipeline(root, shock_vector: dict, **synth_kwargs) -> dict:
    """QueryPipeline.synthesize over the three-document corpus; the REST body."""
    from api.models import synthesis_to_dict
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
    settings.synthesis.numeric_validation = True
    settings.synthesis.drop_invalid = True
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest=FIXED_DIGEST)
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)
    pipe = QueryPipeline(retriever, FakeLLM(responder=responder_for(shock_vector)), None,
                         settings, tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=NullCache())
    syn = pipe.synthesize(QUERY, **synth_kwargs)
    assert {cid(d) for d in DOCS} <= {c.chunk_id for c in syn.query.chunks}
    body = synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
                             diagnostics=syn.diagnostics)
    return json.loads(json.dumps(body))


@pytest.fixture(scope="session")
def svr():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        CODE=CODE, CARD_FIELDS=CARD_FIELDS, TARIFF=TARIFF, DEEPSEEK=DEEPSEEK, NOTE=NOTE,
        cid=cid, chunk=chunk, sf=sf, warns=warns, emitted=emitted,
        run_pipeline=run_pipeline)
