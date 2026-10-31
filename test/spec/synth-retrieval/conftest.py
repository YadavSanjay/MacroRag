"""Fixtures for the synth-retrieval spec tests.

A hand-built corpus whose ranking is known by inspection, indexed into the
in-memory store with the hashing embedder, plus a pipeline builder wired with
the project's sanctioned I/O fakes (FakeLLM, NullCache, disabled tracer) and the
spec's recording fake reranker (C2). Everything lives under a caller-supplied
directory: no repo data dir, no network, no clock.

Ranking by construction (the C1 oracle):
  * every *close* chunk's text is exactly ``QUERY`` and is embedded without the
    grounding prefix, so its dense vector equals the query's (cosine 1, the
    hashing embedder seeds the dense vector by the text) and its sparse dot is
    the maximum possible;
  * every *scenario-study* chunk uses words disjoint from ``QUERY`` (sparse
    dot 0) and a different text (dense cosine < 1).
  So every close chunk outranks every scenario-study chunk in both the dense and
  the sparse ranking, hence in the store's RRF and the cross-branch fusion:
  without the reservation no scenario-study chunk reaches the top ``K`` while
  there are at least ``K`` close chunks.

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

from retriever.rerank import IdentityReranker, Reranker  # noqa: E402

# No vocabulary cue (factor/entity tag or synonym) and no year, so the heuristic
# analyzer adds no facet: with no caller filter every chunk passes the filter.
QUERY = "zorblat quixel frombish wendle"

# Words disjoint from QUERY; each scenario chunk gets a distinct text.
_SCEN_WORDS = ["plinth", "varnok", "tessel", "gromby", "hallux", "murren",
               "pelzik", "quandor", "ruvelt", "snibble"]

FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
FIXED_DIGEST = "abcdef012345"


def chunk_id(doc_id: str) -> str:
    return f"{doc_id}::000"


def _doc(doc_id: str, *, factor_tags: list[str], publisher: str, text: str) -> dict:
    meta = {
        "publisher": publisher, "publication": f"{publisher} {doc_id}",
        "release_date": "2025-05-05", "retrieval_date": "2026-06-19",
        "url": f"https://example.invalid/{doc_id}", "content_hash": doc_id,
        "licence": "public_domain_us_gov", "tier": 7, "bucket": "B",
        "doc_type": "probe_note", "section": None,
        "entity_tags": [], "factor_tags": factor_tags, "trades_affected": [],
    }
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id,
        "doc_type": "probe_note", "publisher": publisher,
        "publication": meta["publication"], "release_date": "2025-05-05",
        "retrieval_date": "2026-06-19", "url": meta["url"],
        "content_hash": doc_id, "tier": 7, "bucket": "B",
        "chunks": [{"chunk_id": chunk_id(doc_id), "seq": 0, "text": text,
                    "metadata": meta}],
        "timeseries": [],
    }


def close_doc(i: int, *, publisher: str = "Desk") -> dict:
    """A non-scenario chunk lexically identical to QUERY."""
    return _doc(f"close_{i:02d}", factor_tags=["equity_vol"], publisher=publisher,
                text=QUERY)


def scenario_doc(i: int, *, publisher: str = "Desk") -> dict:
    """A scenario-study chunk (SCENARIO_STUDY_TAGS) lexically far from QUERY."""
    tags = ["stress_scenario"] if i % 2 else ["cross_asset_scenario"]
    words = " ".join(_SCEN_WORDS[(i + j) % len(_SCEN_WORDS)] for j in range(4))
    return _doc(f"scen_{i:02d}", factor_tags=tags, publisher=publisher,
                text=f"{words} {_SCEN_WORDS[i % len(_SCEN_WORDS)]}")


def is_scenario(cid: str) -> bool:
    return cid.startswith("scen_")


class RecordingReranker(Reranker):
    """The spec's I/O-boundary fake: identity ordering, records each call."""

    def __init__(self) -> None:
        self._inner = IdentityReranker()
        self.calls: list[list[str]] = []

    def rerank(self, query, chunks, *, top_n=None):
        self.calls.append([c.chunk_id for c in chunks])
        return self._inner.rerank(query, chunks, top_n=top_n)


def responder(system, user):
    if "SHOCK VECTOR" in user:
        return json.dumps({"shock_vector": {}})
    return json.dumps({"summary": "scenario narrative", "details": [],
                       "unanswered": []})


def build_pipeline(root, docs: list[dict], *, final_k: int,
                   rerank_candidates: int = 60, fusion_limit: int = 200):
    """(pipe, settings, recorder) over ``docs`` indexed in memory.

    The retriever shares ``settings.retrieval``; neighbour expansion off, MMR off.
    """
    from embeddings.hashing import HashingEmbedder
    from llm.fake import FakeLLM
    from pipelines.query_pipeline import QueryPipeline
    from retriever.retriever import Retriever
    from utils.cache import NullCache
    from utils.config import RagSettings
    from utils.corpus import iter_chunk_points
    from utils.manifest import build_manifest, write_manifest
    from utils.trace import Tracer
    from vectorstores.memory import InMemoryVectorStore

    root = Path(root)
    corpus = root / "processed"
    (corpus / "tierx").mkdir(parents=True, exist_ok=True)
    for doc in docs:
        (corpus / "tierx" / f"{doc['doc_id']}.json").write_text(
            json.dumps(doc), encoding="utf-8")

    embedder = HashingEmbedder(dense_dim=64)
    store = InMemoryVectorStore()
    points = list(iter_chunk_points(corpus, use_grounding=False))
    store.upsert(points, embedder.embed_documents([p.embed_text for p in points]))

    settings = RagSettings()
    r = settings.retrieval
    r.final_k = final_k
    r.rerank_candidates = rerank_candidates
    r.fusion_limit = fusion_limit
    r.dense_limit = fusion_limit
    r.sparse_limit = fusion_limit
    r.chunk_expansion = False
    r.mmr_lambda = 1.0
    r.verify_faithfulness = False
    r.hydrate_timeseries = False
    r.multi_hop_max_hops = 0
    settings.semantic_cache.enabled = False
    settings.escalation.enabled = False
    settings.synthesis.numeric_validation = False
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest=FIXED_DIGEST)
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)

    recorder = RecordingReranker()
    retriever = Retriever(embedder, store, recorder, settings=r)
    pipe = QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=NullCache())
    return pipe, settings, recorder


@pytest.fixture(scope="session")
def sr():
    """Namespace of the corpus helpers above, for the test modules."""
    import types
    return types.SimpleNamespace(
        QUERY=QUERY, chunk_id=chunk_id, close_doc=close_doc,
        scenario_doc=scenario_doc, is_scenario=is_scenario,
        build_pipeline=build_pipeline)
