"""Multi-hop causal retrieval (Priority 6): the iterative hop loop + pipeline
wiring. The loop logic is tested against a stub retriever for determinism (no
embeddings), plus one end-to-end wiring check through QueryPipeline._gather."""

from __future__ import annotations

import json
from types import SimpleNamespace

from llm.fake import FakeLLM
from retriever.multihop import multi_hop_retrieve
from utils.schema import MetaFilter, QueryAnalysis, RetrievedChunk


def _c(cid):
    return RetrievedChunk(cid, f"text {cid}", 1.0, {"publication": "P"})


class _StubRetriever:
    """Maps a query substring -> chunks, so each hop's evidence is deterministic."""
    def __init__(self, mapping, final_k=8):
        self.mapping = mapping
        self.s = SimpleNamespace(final_k=final_k)
        self.calls = []

    def retrieve(self, query, *, base_filter=None):
        self.calls.append(query)
        for key, chunks in self.mapping.items():
            if key in query:
                return list(chunks), QueryAnalysis(original=query, filters=MetaFilter())
        return [], QueryAnalysis(original=query, filters=MetaFilter())


def _responder(script):
    """script: list of dicts returned in order for successive next-hop calls."""
    state = {"i": 0}

    def responder(system, user):
        out = script[min(state["i"], len(script) - 1)]
        state["i"] += 1
        return json.dumps(out)
    return responder


def test_multi_hop_walks_and_dedups():
    r = _StubRetriever({"orig": [_c("a"), _c("b")],
                        "hopA": [_c("b"), _c("c")]})   # b overlaps, c is new
    llm = FakeLLM(responder=_responder([
        {"done": False, "next_query": "hopA question"},
        {"done": True, "next_query": ""},
    ]))
    res = multi_hop_retrieve(r, llm, "orig question", max_hops=3)
    assert res.analysis.route == "MULTI_HOP"
    assert res.hops == ["orig question", "hopA question"]
    ids = [c.chunk_id for c in res.chunks]
    assert sorted(ids) == ["a", "b", "c"] and len(ids) == len(set(ids))   # deduped


def test_multi_hop_stops_when_hop_adds_nothing():
    r = _StubRetriever({"orig": [_c("a"), _c("b")],
                        "dup": [_c("a")]})            # only already-seen chunks
    llm = FakeLLM(responder=_responder([{"done": False, "next_query": "dup question"}]))
    res = multi_hop_retrieve(r, llm, "orig question", max_hops=4)
    assert res.hops == ["orig question"]              # dup hop not appended
    assert sorted(c.chunk_id for c in res.chunks) == ["a", "b"]


def test_multi_hop_done_immediately():
    r = _StubRetriever({"orig": [_c("a")]})
    llm = FakeLLM(responder=_responder([{"done": True, "next_query": ""}]))
    res = multi_hop_retrieve(r, llm, "orig question", max_hops=3)
    assert res.hops == ["orig question"]
    assert len(llm.calls) == 1                        # one (done) planning call


def test_multi_hop_respects_max_hops():
    # planner always wants more, each hop adds a new chunk; max_hops caps it at 2
    r = _StubRetriever({"orig": [_c("a")], "q": [_c("b"), _c("c"), _c("d"), _c("e")]})

    def responder(system, user):
        return json.dumps({"done": False, "next_query": "q hop"})
    res = multi_hop_retrieve(r, FakeLLM(responder=responder), "orig", max_hops=2)
    assert len(res.hops) == 2                         # original + 1 (capped)


def test_max_hops_one_is_single_shot():
    r = _StubRetriever({"orig": [_c("a")]})
    llm = FakeLLM(responder=_responder([{"done": False, "next_query": "x"}]))
    res = multi_hop_retrieve(r, llm, "orig", max_hops=1)
    assert res.hops == ["orig"] and len(llm.calls) == 0   # no next-hop planning
    assert res.analysis.route == "MULTI_HOP"


def test_config_multi_hop_default_off():
    from utils.config import RetrievalSettings
    assert RetrievalSettings().multi_hop_max_hops == 0


def test_pipeline_gather_routes_to_multi_hop(indexed, tmp_path):
    from pipelines.query_pipeline import QueryPipeline
    from retriever.rerank import IdentityReranker
    from retriever.retriever import Retriever
    from utils.config import RagSettings, RetrievalSettings
    from utils.trace import Tracer

    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=3, multi_hop_max_hops=3))
    settings = RagSettings()
    settings.retrieval.multi_hop_max_hops = 3
    # planner returns done immediately -> one real retrieval, MULTI_HOP route
    llm = FakeLLM(responder=lambda system, user: json.dumps({"done": True, "next_query": ""}))
    pipe = QueryPipeline(retriever, llm, None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False), cache=None)
    chunks, analysis, hits = pipe._gather("severely adverse stress test", None, {})
    assert analysis.route == "MULTI_HOP"
    assert chunks                                    # real retrieval still returned evidence
