"""Fixtures for the synth-direction spec tests.

C1/C2 build ``ShockField`` instances by hand. C3/C4 build the in-memory pipeline
(hashing embedder, in-memory store, identity reranker, scripted FakeLLM, NullCache,
disabled tracer) over one dated document whose text holds no number-and-unit quantity,
and drive it directly, over REST (FastAPI TestClient) and over MCP. Everything lives
under a caller-supplied directory: no repo data dir, no network, no clock.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

CODE = "direction_mismatch"
FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
FIXED_DIGEST = "abcdef012345"

DOC = "episode_note"
CID = f"{DOC}::000"
DOC_TEXT = ("Over the following sessions the currency weakened, the haven currency "
            "rallied, credit spreads widened and equity volatility rose.")
# No whole-word vocabulary cue and no year: no facet filter, so the chunk is retrieved.
QUERY = "how would markets move under this shock"

# spec C3: the 2026-10-14 live taiwan_geopolitical values.
LIVE = {
    "USDTWD": (-2.32, "pct"),
    "USDJPY": (-4.95, "pct"),
    "IG_OAS": (25.0, "bp"),
    "VIX": (30.82, "pts"),
}
LIVE_DIRECTIONS = {"USDTWD": "up", "USDJPY": "down", "IG_OAS": "up", "VIX": "up"}


def sf(name: str, value: float, unit: str = "bp", *, validation: list[str] | None = None):
    from synthesis.schema import ShockField
    return ShockField(name, value, unit, chunk_ids=[CID], rationale="",
                      grounding_mode="direct", validation=list(validation or []))


def warns(diag, field: str | None = None) -> list[dict]:
    """Every direction_mismatch warning (for ``field`` when given)."""
    return [w for w in diag.validation
            if w.get("code") == CODE and (field is None or w.get("field") == field)]


def scripted(values: dict[str, tuple[float, str]]) -> dict:
    return {name: {"value": v, "unit": u, "chunk_ids": [CID], "grounding_mode": "direct",
                   "rationale": "episode move"}
            for name, (v, u) in values.items()}


def responder_for(shock_vector: dict):
    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": shock_vector})
        return json.dumps({"summary": "scenario narrative", "details": [],
                           "unanswered": []})
    return responder


def _doc() -> dict:
    meta = {
        "publisher": "MacroRAG", "publication": "Episode note",
        "release_date": "2026-02-05", "retrieval_date": "2026-06-19",
        "url": f"https://example.invalid/{DOC}", "content_hash": DOC,
        "licence": "public_domain_us_gov", "tier": 3, "bucket": "C",
        "doc_type": "research_note", "section": None, "entity_tags": [],
        "factor_tags": ["cross_asset_scenario"], "trades_affected": [],
    }
    return {
        "doc_id": DOC, "source_id": DOC, "title": DOC, "doc_type": "research_note",
        "publisher": "MacroRAG", "publication": meta["publication"],
        "release_date": "2026-02-05", "retrieval_date": "2026-06-19", "url": meta["url"],
        "content_hash": DOC, "tier": 3, "bucket": "C",
        "chunks": [{"chunk_id": CID, "seq": 0, "text": DOC_TEXT, "metadata": meta}],
        "timeseries": [],
    }


def build_pipeline(root, values: dict[str, tuple[float, str]] | None = None, *,
                   drop_invalid: bool = True, direction_check: bool = True):
    """(pipe, settings): one dated document indexed in memory, scripted model."""
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
    (corpus / "tierx" / f"{DOC}.json").write_text(json.dumps(_doc()), encoding="utf-8")

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
    settings.synthesis.numeric_validation = True
    settings.synthesis.drop_invalid = drop_invalid
    settings.synthesis.direction_check = direction_check
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest=FIXED_DIGEST)
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)

    pipe = QueryPipeline(retriever,
                         FakeLLM(responder=responder_for(scripted(values or LIVE))),
                         None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=NullCache())
    return pipe, settings


def body_of(syn) -> dict:
    """The REST serialization of a SynthesisResult, JSON-normalised like the wire."""
    from api.models import synthesis_to_dict
    body = synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
                             diagnostics=syn.diagnostics)
    return json.loads(json.dumps(body))


def rest_client(pipe, settings):
    from fastapi.testclient import TestClient

    from api.app import create_app
    return TestClient(create_app(lambda: pipe, settings))


def mcp_call(pipe, settings, args: dict) -> dict:
    from mcp_server.server import build_mcp
    mcp = build_mcp(lambda: pipe, settings)
    blocks = asyncio.run(mcp.call_tool("macro_synthesize_shock_vector", args))
    return json.loads(blocks[0].text)


def dropped_names(body: dict) -> set[str]:
    return {d.get("field") for d in body["synthesis_diagnostics"]["dropped"]}


def body_warns(body: dict) -> list[dict]:
    return [w for w in body["synthesis_diagnostics"]["validation"] if w.get("code") == CODE]


@pytest.fixture(scope="session")
def sd():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        CODE=CODE, CID=CID, QUERY=QUERY, LIVE=LIVE, LIVE_DIRECTIONS=LIVE_DIRECTIONS,
        sf=sf, warns=warns, build_pipeline=build_pipeline, body_of=body_of,
        rest_client=rest_client, mcp_call=mcp_call, dropped_names=dropped_names,
        body_warns=body_warns)
