"""Fixtures for the synth-card-value spec tests.

Card chunk texts in the exact market-episodes line format (the Treasury refunding card's
10-day lines as stored on 2026-10-15), hand-built fields and chunks, and for C3 the in-memory
pipeline (hashing embedder, in-memory store, identity reranker, scripted FakeLLM, NullCache,
disabled tracer). No network, no ``data/``, no clock.
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

CODE = "card_value_mismatch"
CARD = "market_episode_treasury_refunding_2023"
CARD_CID = f"{CARD}::001"
NOTE_CID = "rates_note::000"
QUERY = "how would markets move under this shock"
FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
FIXED_DIGEST = "abcdef012345"

_IG = ("IG_OAS stand-in (BAA10Y, Moody's Baa yield minus 10-year Treasury; a yield spread, "
       "not an OAS)")


def line(label: str, end: str, unit: str, peak: str, k: int = 10,
         d: str = "2023-08-14") -> str:
    return (f"- {label}: {end} {unit} after {k} trading days ({d}); "
            f"largest move within the window {peak} {unit}")


CARD_TEXT = "\n".join([
    "Market episode: Treasury refunding and Fitch downgrade. Realized moves over the 10 "
    "trading days after the 2023-07-31 close, from FRED daily data.",
    line("VIX (VIXCLS)", "+1.19", "pts", "+3.47"),
    line("UST_2Y (DGS2)", "+8.0", "bp", "-14.0"),
    line("UST_10Y (DGS10)", "+22.0", "bp", "+23.0"),
    line(_IG, "+7.0", "bp", "+16.0"),
    line("USDJPY (DEXJPUS)", "+2.19", "pct", "+2.19"),
    line("THREEFYTP10 (Kim-Wright 10-year term premium)", "+9.8", "bp", "+9.8"),
])
NOTE_TEXT = "Treasury yields rose as issuance expectations increased."


def chunk(cid: str, text: str, doc_type: str):
    from utils.schema import RetrievedChunk
    payload = {"release_date": "2023-08-14", "publisher": "X", "publication": "Y",
               "doc_id": cid.split("::")[0], "doc_type": doc_type}
    return RetrievedChunk(cid, text, 1.0, payload)


def card_chunk(text: str = CARD_TEXT, cid: str = CARD_CID):
    return chunk(cid, text, "market_episode")


def note_chunk():
    return chunk(NOTE_CID, NOTE_TEXT, "research_note")


def sf(name: str, value: float, unit: str, cids: list[str], *,
       validation: list[str] | None = None):
    from synthesis.schema import ShockField
    return ShockField(name, value, unit, chunk_ids=list(cids), rationale="",
                      grounding_mode="analog", validation=list(validation or []))


def warns(diag) -> list[dict]:
    return [w for w in diag.validation if w.get("code") == CODE]


def _doc(doc_id: str, text: str, doc_type: str, seq: int) -> dict:
    meta = {
        "publisher": "MacroRAG", "publication": doc_id, "release_date": "2023-08-14",
        "retrieval_date": "2026-10-15", "url": f"https://example.invalid/{doc_id}",
        "content_hash": doc_id, "licence": "derived_fred", "tier": 3, "bucket": "C",
        "doc_type": doc_type, "section": None, "entity_tags": [],
        "factor_tags": ["cross_asset_scenario"], "trades_affected": [],
    }
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id, "doc_type": doc_type,
        "publisher": "MacroRAG", "publication": doc_id, "release_date": "2023-08-14",
        "retrieval_date": "2026-10-15", "url": meta["url"], "content_hash": doc_id,
        "tier": 3, "bucket": "C",
        "chunks": [{"chunk_id": f"{doc_id}::{seq:03d}", "seq": seq, "text": text,
                    "metadata": meta}],
        "timeseries": [],
    }


def run_pipeline(root, shock_vector: dict, **synth_kwargs) -> dict:
    """QueryPipeline.synthesize (drop_invalid on) over the card + a note; the REST body."""
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
    for d in (_doc(CARD, CARD_TEXT, "market_episode", 1),
              _doc("rates_note", NOTE_TEXT, "research_note", 0)):
        (corpus / "tierx" / f"{d['doc_id']}.json").write_text(json.dumps(d), encoding="utf-8")
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

    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": shock_vector})
        return json.dumps({"summary": "narrative", "details": [], "unanswered": []})

    pipe = QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=NullCache())
    syn = pipe.synthesize(QUERY, **synth_kwargs)
    assert CARD_CID in {c.chunk_id for c in syn.query.chunks}
    body = synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
                             diagnostics=syn.diagnostics)
    return json.loads(json.dumps(body))


@pytest.fixture(scope="session")
def scv():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        CODE=CODE, CARD_CID=CARD_CID, NOTE_CID=NOTE_CID, CARD_TEXT=CARD_TEXT, line=line,
        card_chunk=card_chunk, note_chunk=note_chunk, sf=sf, warns=warns,
        run_pipeline=run_pipeline)
