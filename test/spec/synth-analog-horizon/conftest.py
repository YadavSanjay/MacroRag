"""Fixtures for the synth-analog-horizon spec tests.

C1 helpers build ``ShockField`` and dated ``RetrievedChunk`` instances by hand,
with payloads carrying ``doc_type`` and, where needed, ``horizon_days``.
C2 drives ``synthesize_shock_vector`` with a scripted FakeLLM.
C3 builds the in-memory pipeline (hashing embedder, in-memory store, identity
reranker, scripted FakeLLM, NullCache, disabled tracer) over a three-document
corpus carrying the live case's evidence. Everything lives under a caller-supplied
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

CODE_MISMATCH = "horizon_mismatch"
CODE_UNKNOWN = "horizon_unknown"
CODES = (CODE_MISMATCH, CODE_UNKNOWN)

# The spec's doc_type -> minimum-horizon table (days), copied verbatim.
MIN_DAYS = {
    "dfast_scenarios": 90,
    "dfast_scenario_data": 90,
    "dfast_results": 90,
    "eba_esrb_adverse_scenario": 365,
}

# C3 corpus (live case): a DFAST severely adverse path, a TBAC presentation, and a
# plain research note. Texts carry no number+unit quantity, so the unit/scale check
# of synth-analog-quality stays inert on them.
DFAST = "dfast_2026_severely_adverse"
TBAC = "tbac_presentation_q3"
NOTE = "rates_research_note"
DOCS = {
    DFAST: ("dfast_scenarios",
            "Under the severely adverse scenario corporate bond spreads widen sharply "
            "and peak several quarters into the projection horizon."),
    TBAC: ("tbac_presentation",
           "Treasury yields moved higher across the curve over the past quarter as "
           "term premia rebuilt."),
    NOTE: ("research_note",
           "Short-dated yields reacted quickly to the policy surprise in the "
           "following sessions."),
}

# No whole-word vocabulary cue and no year in the query: no facet filter, so all
# three dated corpus chunks are retrieved (final_k=10 > 3).
QUERY = "how would markets move under this shock"


def chunk_id(doc_id: str) -> str:
    return f"{doc_id}::000"


def chunk(cid: str, *, doc_type: str | None = None, horizon=None,
          text: str = "evidence for the move"):
    """A dated retrieved chunk; ``horizon`` (any value) goes in payload["horizon_days"]."""
    from utils.schema import RetrievedChunk
    payload: dict = {"release_date": "2026-02-05", "publisher": "X",
                     "publication": "Y", "doc_id": cid.split("::")[0]}
    if doc_type is not None:
        payload["doc_type"] = doc_type
    if horizon is not None:
        payload["horizon_days"] = horizon
    return RetrievedChunk(cid, text, 1.0, payload)


def sf(name: str, value: float, unit: str, chunk_ids: list[str], *,
       declared=None, mode: str = "direct", validation: list[str] | None = None):
    from synthesis.schema import ShockField
    return ShockField(name, value, unit, chunk_ids=list(chunk_ids),
                      rationale="", grounding_mode=mode,
                      validation=list(validation or []),
                      source_horizon_days=declared)


def warns(diag, field: str) -> list[dict]:
    """Every horizon_* warning recorded for ``field``."""
    return [w for w in diag.validation
            if w.get("code") in CODES and w.get("field") == field]


def make_doc(doc_id: str) -> dict:
    doc_type, text = DOCS[doc_id]
    meta = {
        "publisher": "FRB", "publication": f"FRB {doc_id}",
        "release_date": "2026-02-05", "retrieval_date": "2026-06-19",
        "url": f"https://example.invalid/{doc_id}", "content_hash": doc_id,
        "licence": "public_domain_us_gov", "tier": 1, "bucket": "B",
        "doc_type": doc_type, "section": None, "entity_tags": [],
        "factor_tags": ["stress_scenario"], "trades_affected": [],
    }
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id,
        "doc_type": doc_type, "publisher": "FRB",
        "publication": meta["publication"], "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": meta["url"],
        "content_hash": doc_id, "tier": 1, "bucket": "B",
        "chunks": [{"chunk_id": chunk_id(doc_id), "seq": 0, "text": text,
                    "metadata": meta}],
        "timeseries": [],
    }


def live_shock_vector() -> dict:
    """The live case plus three UST legs, as a scripted model emits them.

    IG_OAS +440 bp cites the DFAST chunk (the live case). UST_2Y +50 bp also cites
    it (plausible in size at 10 days, so only the horizon check can flag it).
    UST_10Y cites the TBAC chunk with no declared horizon; UST_5Y declares 10 days;
    UST_30Y declares 90 days (the TBAC 3-month move).
    """
    return {
        "IG_OAS": {"value": 440, "unit": "bp", "chunk_ids": [chunk_id(DFAST)],
                   "grounding_mode": "direct",
                   "rationale": "severely adverse path for corporate spreads"},
        "UST_2Y": {"value": 50, "unit": "bp", "chunk_ids": [chunk_id(DFAST)],
                   "grounding_mode": "direct", "rationale": "adverse path"},
        "UST_10Y": {"value": 15, "unit": "bp", "chunk_ids": [chunk_id(TBAC)],
                    "grounding_mode": "direct", "rationale": "term premia rebuild"},
        "UST_5Y": {"value": 20, "unit": "bp", "chunk_ids": [chunk_id(NOTE)],
                   "grounding_mode": "direct", "source_horizon_days": 10,
                   "rationale": "quick reaction to policy surprise"},
        "UST_30Y": {"value": 25, "unit": "bp", "chunk_ids": [chunk_id(TBAC)],
                    "grounding_mode": "direct", "source_horizon_days": 90,
                    "rationale": "quarterly move in long yields"},
    }


def responder_for(shock_vector: dict):
    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": shock_vector})
        return json.dumps({"summary": "scenario narrative", "details": [],
                           "unanswered": []})
    return responder


def build_pipeline(root, responder, *, numeric_validation: bool = True,
                   drop_invalid: bool = False, horizon_check: bool = True,
                   horizon_tolerance: float = 3.0):
    """(pipe, settings) over the three live-case documents indexed in memory."""
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
        doc = make_doc(doc_id)
        (corpus / "tierx" / f"{doc_id}.json").write_text(json.dumps(doc), encoding="utf-8")

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
    settings.synthesis.horizon_check = horizon_check
    settings.synthesis.horizon_tolerance = horizon_tolerance
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
def sah():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        CODE_MISMATCH=CODE_MISMATCH, CODE_UNKNOWN=CODE_UNKNOWN, CODES=CODES,
        MIN_DAYS=MIN_DAYS, DFAST=DFAST, TBAC=TBAC, NOTE=NOTE, QUERY=QUERY,
        chunk_id=chunk_id, chunk=chunk, sf=sf, warns=warns,
        live_shock_vector=live_shock_vector, responder_for=responder_for,
        build_pipeline=build_pipeline)
