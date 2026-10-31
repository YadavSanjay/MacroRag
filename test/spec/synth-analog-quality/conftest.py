"""Fixtures for the synth-analog-quality spec tests.

C1/C2 helpers build ``ShockField`` and dated ``RetrievedChunk`` instances by hand.
C3 builds the in-memory pipeline (hashing embedder, in-memory store, identity
reranker, scripted FakeLLM, NullCache, disabled tracer) over a two-document corpus
carrying the live case's evidence. Everything lives under a caller-supplied
directory: no repo data dir, no network, no clock.
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

# Live-case evidence (spec Requirement): the GFSR sentence and the UAH analog.
GFSR = "imf_gfsr_geopolitical_ch2"
UAH = "fx_episode_note"
GFSR_TEXT = ("Historical evidence shows that sovereign CDS spreads generally increase "
             "more than 1 percent cumulatively one week after major global "
             "geopolitical risk events.")
UAH_TEXT = ("During the 2014 episode the USD/UAH rose about 5 percent as the "
            "hryvnia came under pressure.")

# No whole-word vocabulary cue and no year in the query: no facet filter, so both
# dated corpus chunks are retrieved (final_k=10 > 2).
QUERY = "how would markets move under this shock"

CODE_UNIT = "unit_scale_mismatch"
CODE_FX = "fx_proxy_unlisted"


def chunk_id(doc_id: str) -> str:
    return f"{doc_id}::000"


def chunk(cid: str, text: str):
    """A dated retrieved chunk with the given id and text."""
    from utils.schema import RetrievedChunk
    return RetrievedChunk(cid, text, 1.0, {"release_date": "2025-04-22",
                                           "publisher": "X", "publication": "Y",
                                           "doc_id": cid.split("::")[0]})


def sf(name: str, value: float, unit: str, chunk_ids: list[str], *,
       rationale: str = "", mode: str = "direct"):
    from synthesis.schema import ShockField
    return ShockField(name, value, unit, chunk_ids=list(chunk_ids),
                      rationale=rationale, grounding_mode=mode)


def warns(diag, code: str, field: str) -> list[dict]:
    return [w for w in diag.validation
            if w.get("code") == code and w.get("field") == field]


def make_doc(doc_id: str, text: str) -> dict:
    meta = {
        "publisher": "IMF", "publication": f"IMF {doc_id}",
        "release_date": "2025-04-22", "retrieval_date": "2026-06-19",
        "url": f"https://example.invalid/{doc_id}", "content_hash": doc_id,
        "licence": "public_domain_us_gov", "tier": 7, "bucket": "B",
        "doc_type": "gfsr_ch2", "section": None, "entity_tags": [],
        "factor_tags": ["stress_scenario"], "trades_affected": [],
    }
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id,
        "doc_type": "gfsr_ch2", "publisher": "IMF",
        "publication": meta["publication"], "release_date": "2025-04-22",
        "retrieval_date": "2026-06-19", "url": meta["url"],
        "content_hash": doc_id, "tier": 7, "bucket": "B",
        "chunks": [{"chunk_id": chunk_id(doc_id), "seq": 0, "text": text,
                    "metadata": meta}],
        "timeseries": [],
    }


def live_shock_vector() -> dict:
    """The live case's two analog fields, as the model emitted them."""
    return {
        "IG_OAS": {"value": 1, "unit": "bp", "chunk_ids": [chunk_id(GFSR)],
                   "grounding_mode": "analog",
                   "rationale": "CDS spreads increase more than 1 percent after "
                                "geopolitical risk events"},
        "USDTWD": {"value": 5, "unit": "pct", "chunk_ids": [chunk_id(UAH)],
                   "grounding_mode": "analog",
                   "rationale": "USD/UAH rose about 5 percent"},
    }


def responder_for(shock_vector: dict):
    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": shock_vector})
        return json.dumps({"summary": "scenario narrative", "details": [],
                           "unanswered": []})
    return responder


def build_pipeline(root, responder, *, numeric_validation: bool,
                   drop_invalid: bool):
    """(pipe, settings) over the two live-case documents indexed in memory."""
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
    for doc in (make_doc(GFSR, GFSR_TEXT), make_doc(UAH, UAH_TEXT)):
        (corpus / "tierx" / f"{doc['doc_id']}.json").write_text(
            json.dumps(doc), encoding="utf-8")

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
    settings.synthesis.enable_analog = True
    settings.synthesis.numeric_validation = numeric_validation
    settings.synthesis.drop_invalid = drop_invalid
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest=FIXED_DIGEST)
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)

    pipe = QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=NullCache())
    return pipe, settings


@pytest.fixture(scope="session")
def saq():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        GFSR=GFSR, UAH=UAH, GFSR_TEXT=GFSR_TEXT, UAH_TEXT=UAH_TEXT, QUERY=QUERY,
        CODE_UNIT=CODE_UNIT, CODE_FX=CODE_FX, chunk_id=chunk_id, chunk=chunk,
        sf=sf, warns=warns, live_shock_vector=live_shock_vector,
        responder_for=responder_for, build_pipeline=build_pipeline)
