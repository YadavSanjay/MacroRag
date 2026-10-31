"""Fixtures for the synth-grounding spec tests.

A hand-built four-document corpus (C2's EBA-like / GFSR-like / Taiwan-semis /
distractor set from the spec's Notes), indexed into the in-memory store with the
hashing embedder, and a pipeline builder wired with the project's sanctioned
I/O fakes (FakeLLM, IdentityReranker, NullCache, disabled tracer). Everything
lives under a caller-supplied directory: no repo data dir, no network, no clock.

The builder is a session-scoped fixture returning a plain function so that
hypothesis tests can call it per example inside their own temporary directory.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Fixed manifest stamp: the real world_view_id is `macro-<built_at date>-<digest>`
# (CLAUDE.md, world_view_id definition), so this manifest's id is known by hand.
FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
FIXED_DIGEST = "abcdef012345"
FIXED_WORLD_VIEW_ID = "macro-2026-01-02-abcdef012345"

# C2 corpus doc ids (one chunk each, chunk id `<doc_id>::000`).
EBA = "eba_adverse"
GFSR = "imf_gfsr_ch2"
TAIWAN = "taiwan_semis"
DISTRACTOR = "equity_vol_note"


def chunk_id(doc_id: str) -> str:
    return f"{doc_id}::000"


def make_doc(doc_id: str, *, factor_tags: list[str], entity_tags: list[str],
             tier: int, publisher: str, doc_type: str, release_date: str,
             text: str) -> dict:
    meta = {
        "publisher": publisher, "publication": f"{publisher} {doc_id}",
        "release_date": release_date, "retrieval_date": "2026-06-19",
        "url": f"https://example.invalid/{doc_id}", "content_hash": doc_id,
        "licence": "public_domain_us_gov", "tier": tier, "bucket": "B",
        "doc_type": doc_type, "section": None,
        "entity_tags": entity_tags, "factor_tags": factor_tags,
        "trades_affected": [],
    }
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id,
        "doc_type": doc_type, "publisher": publisher,
        "publication": meta["publication"], "release_date": release_date,
        "retrieval_date": "2026-06-19", "url": meta["url"],
        "content_hash": doc_id, "tier": tier, "bucket": "B",
        "chunks": [{"chunk_id": chunk_id(doc_id), "seq": 0, "text": text,
                    "metadata": meta}],
        "timeseries": [],
    }


# Texts are deliberately close to the C2 query wording so the hashing embedder
# ranks every admitted chunk; final_k is generous so ranking never decides
# membership.
_TEXT = ("geopolitical shock to equity vol from a semiconductor export blockade: "
         "{extra}")


def scenario_corpus() -> list[dict]:
    """The spec's four-document C2 corpus (tags mirror tier7_scenarios.yaml)."""
    return [
        make_doc(EBA,
                 factor_tags=["cross_asset_scenario", "stress_scenario",
                              "geopolitical_risk", "equity_vol", "fx"],
                 entity_tags=[], tier=7, publisher="EBA",
                 doc_type="eba_stress_scenario", release_date="2025-01-31",
                 text=_TEXT.format(extra="adverse scenario equity prices fall 50 "
                                         "percent and volatility doubles")),
        make_doc(GFSR,
                 factor_tags=["geopolitical_risk", "cross_asset_scenario",
                              "equity_vol", "china_taiwan_tail"],
                 entity_tags=["china"], tier=7, publisher="IMF",
                 doc_type="gfsr_ch2", release_date="2025-04-22",
                 text=_TEXT.format(extra="major geopolitical risk events lower "
                                         "equity returns by 1 percent per month")),
        make_doc(TAIWAN,
                 factor_tags=["china_taiwan_tail", "geopolitical_risk",
                              "supply_chain_disruption"],
                 entity_tags=["china", "semiconductors"], tier=6,
                 publisher="Think Tank", doc_type="iea_report",
                 release_date="2025-06-01",
                 text=_TEXT.format(extra="a blockade would halt advanced node "
                                         "output with qualitative disruption")),
        make_doc(DISTRACTOR,
                 factor_tags=["equity_vol"], entity_tags=[], tier=7,
                 publisher="Desk", doc_type="cboe_note",
                 release_date="2025-03-03",
                 text=_TEXT.format(extra="equity vol term structure commentary")),
    ]


def build_pipeline(root: Path, responder, *, docs: list[dict] | None = None,
                   write_fixed_manifest: bool = True, final_k: int = 10):
    """(pipe, settings) over ``docs`` (default: the C2 corpus) indexed in memory."""
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
    for doc in (scenario_corpus() if docs is None else docs):
        (corpus / "tierx" / f"{doc['doc_id']}.json").write_text(
            json.dumps(doc), encoding="utf-8")

    embedder = HashingEmbedder(dense_dim=64)
    store = InMemoryVectorStore()
    points = list(iter_chunk_points(corpus))
    store.upsert(points, embedder.embed_documents([p.embed_text for p in points]))

    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=final_k))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    settings.retrieval.hydrate_timeseries = False
    settings.semantic_cache.enabled = False
    settings.escalation.enabled = False
    # C3: `grounded` must not hinge on validation defaults (drop_invalid etc.).
    settings.synthesis.numeric_validation = False
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    if write_fixed_manifest:
        (root / "vectorstore").mkdir(parents=True, exist_ok=True)
        manifest = build_manifest(settings, chunks=len(points), digest=FIXED_DIGEST)
        manifest["built_at"] = FIXED_BUILT_AT
        write_manifest(settings.manifest_path, manifest)

    pipe = QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=NullCache())
    return pipe, settings


def answer_json(summary: str, details: list[dict] | None = None) -> str:
    return json.dumps({"summary": summary, "details": details or [],
                       "unanswered": []})


@pytest.fixture(scope="session")
def make_pipeline():
    """Session-scoped so hypothesis tests may use it (it holds no state)."""
    return build_pipeline


@pytest.fixture(scope="session")
def sg():
    """Namespace of the corpus constants/helpers above, for the test modules."""
    import types
    return types.SimpleNamespace(
        EBA=EBA, GFSR=GFSR, TAIWAN=TAIWAN, DISTRACTOR=DISTRACTOR,
        chunk_id=chunk_id, make_doc=make_doc, scenario_corpus=scenario_corpus,
        answer_json=answer_json, FIXED_WORLD_VIEW_ID=FIXED_WORLD_VIEW_ID,
        FIXED_BUILT_AT=FIXED_BUILT_AT, FIXED_DIGEST=FIXED_DIGEST)
