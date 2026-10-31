"""Fixtures for the synth-direction-prompt spec tests.

C1 drives ``synthesize_shock_vector`` directly with a recording FakeLLM over one
dated chunk. C2 builds the in-memory pipeline (hashing embedder, in-memory store,
identity reranker, recording FakeLLM, NullCache, disabled tracer) over one dated
document and reads the synthesize prompt the model received. Everything lives under a
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

FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
FIXED_DIGEST = "abcdef012345"
DOC = "episode_note"
CID = f"{DOC}::000"
QUERY = "how would markets move under this shock"

PREFIX = ("- Expected directions (from the scenario; ground each field on evidence that "
          "moved this way): ")
# The shock-field sign conventions as published (guide 05 §5, synthesis/vocab.py), in
# SHOCK_FIELDS order, copied by hand.
POSITIVE_MEANS = {
    "UST_2Y": "yields rise", "UST_5Y": "yields rise", "UST_10Y": "yields rise",
    "UST_30Y": "yields rise", "UST": "yields rise (parallel shift)",
    "IG_OAS": "spreads widen", "HY_OAS": "spreads widen",
    "MOVE": "rates vol rises (aggregate/parallel)",
    "MOVE_1Y": "rates vol rises (~1Y expiry)", "MOVE_5Y": "rates vol rises (~5Y expiry)",
    "MOVE_10Y": "rates vol rises (~10Y expiry)", "MOVE_30Y": "rates vol rises (~30Y expiry)",
    "VIX": "equity vol rises", "EQUITY_US": "equity index rises",
    "USDTWD": "TWD depreciates", "USDJPY": "JPY depreciates", "XCCY_BASIS": "—",
}
ORDER = list(POSITIVE_MEANS)

# Today's CONSTRAINTS lines (the existing rendering rule, written out by hand).
HORIZON_10 = ("- Scenario horizon: 10 day(s). Every move is the cumulative change over "
              "this horizon.")


def fields_line(fields: list[str]) -> str:
    return ("- Emit ONLY these fields (still omit any you cannot ground in the evidence): "
            f"{', '.join(fields)}.")


def expected_line(directions: dict | None) -> str | None:
    """The spec's directions line, from the hand-copied table above."""
    entries = []
    upper = {str(k).upper(): v for k, v in (directions or {}).items()}
    for name in ORDER:
        d = upper.get(name)
        if d not in ("up", "down"):
            continue
        sign = "+" if d == "up" else "-"
        entries.append(f"{name} {d} ({sign}, positive means {POSITIVE_MEANS[name]})")
    return PREFIX + "; ".join(entries) + "." if entries else None


class Recorder:
    """A FakeLLM responder that records each user prompt and returns no fields."""

    def __init__(self):
        self.users: list[str] = []

    def __call__(self, system, user):
        self.users.append(user)
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": {}})
        return json.dumps({"summary": "scenario narrative", "details": [],
                           "unanswered": []})

    def synth_prompts(self) -> list[str]:
        return [u for u in self.users if "SHOCK VECTOR" in u]


def chunk():
    from utils.schema import RetrievedChunk
    payload = {"release_date": "2026-02-05", "publisher": "X", "publication": "Y",
               "doc_id": DOC, "doc_type": "research_note"}
    return RetrievedChunk(CID, "Credit spreads widened in the following sessions.", 1.0,
                          payload)


def synth_prompt(**kwargs) -> str:
    """The user prompt synthesize_shock_vector sends, for the given keyword args."""
    from llm.fake import FakeLLM
    from synthesis.synthesizer import synthesize_shock_vector
    rec = Recorder()
    synthesize_shock_vector(FakeLLM(responder=rec), QUERY, [chunk()], **kwargs)
    prompts = rec.synth_prompts()
    assert len(prompts) == 1, len(prompts)
    return prompts[0]


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
        "chunks": [{"chunk_id": CID, "seq": 0,
                    "text": "Credit spreads widened in the following sessions.",
                    "metadata": meta}],
        "timeseries": [],
    }


def pipeline_prompts(root, **synth_kwargs) -> list[str]:
    """Run QueryPipeline.synthesize on the one-document corpus; return the synthesize
    prompts the model received."""
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
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest=FIXED_DIGEST)
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)
    rec = Recorder()
    pipe = QueryPipeline(retriever, FakeLLM(responder=rec), None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=NullCache())
    syn = pipe.synthesize(QUERY, **synth_kwargs)
    assert CID in {c.chunk_id for c in syn.query.chunks}
    return rec.synth_prompts()


@pytest.fixture(scope="session")
def sdp():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        PREFIX=PREFIX, ORDER=ORDER, POSITIVE_MEANS=POSITIVE_MEANS, HORIZON_10=HORIZON_10,
        fields_line=fields_line, expected_line=expected_line, synth_prompt=synth_prompt,
        pipeline_prompts=pipeline_prompts)
