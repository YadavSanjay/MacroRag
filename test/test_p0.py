"""P0 hardening: relevance-floor gate, faithfulness verifier, parallel branch
fan-out, and the structured query trace."""

from __future__ import annotations

import json

from llm.fake import FakeLLM
from llm.verify import verify_answer
from pipelines.query_pipeline import QueryPipeline
from retriever.rerank import IdentityReranker
from retriever.retriever import Retriever
from utils.config import RagSettings, RetrievalSettings
from utils.schema import Answer, AnswerDetail, MetaFilter, QueryAnalysis, RetrievedChunk
from utils.trace import Tracer, build_trace


def _pipeline(indexed, tmp_path, llm, *, min_score=0.0, verify=False):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    settings = RagSettings()
    settings.retrieval.min_rerank_score = min_score
    settings.retrieval.verify_faithfulness = verify
    return QueryPipeline(retriever, llm, None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False))


# ── P0.1 relevance-floor gate ────────────────────────────────────────────────

def test_gate_refuses_below_floor_without_calling_llm(indexed, tmp_path):
    llm = FakeLLM(responses=['{"summary":"should not be used","details":[]}'])
    # IdentityReranker scores are small RRF values; an absurd floor forces refusal
    pipe = _pipeline(indexed, tmp_path, llm, min_score=99.0)
    result = pipe.answer("severely adverse unemployment stress test")
    assert result.answer.refused is True
    assert "couldn't find" in result.answer.summary.lower()
    assert llm.calls == []          # generation skipped entirely


def test_gate_passes_when_floor_zero(indexed, tmp_path):
    llm = FakeLLM(responses=['{"summary":"ok","details":[],"unanswered":[]}'])
    pipe = _pipeline(indexed, tmp_path, llm, min_score=0.0)
    result = pipe.answer("severely adverse unemployment stress test")
    assert result.answer.refused is False
    assert llm.calls            # generation happened


# ── P0.2 faithfulness verifier ───────────────────────────────────────────────

def test_verifier_drops_unsupported_claim():
    chunks = [RetrievedChunk("c1", "Unemployment peaks at 10 percent.", 1.0, {})]
    answer = Answer(summary="...", details=[
        AnswerDetail("Unemployment peaks at 10%.", ["c1"]),
        AnswerDetail("Inflation falls to 1%.", ["c1"]),   # not in chunk
    ])
    verdict = json.dumps({"results": [
        {"index": 0, "supported": True}, {"index": 1, "supported": False}]})
    out = verify_answer(FakeLLM(responses=[verdict]), "q", answer, chunks)
    assert [d.text for d in out.details] == ["Unemployment peaks at 10%."]
    assert out.unsupported == ["Inflation falls to 1%."]
    assert out.used_chunk_ids == ["c1"]


def test_verifier_fails_open_on_unparseable_verdict():
    answer = Answer(summary="s", details=[AnswerDetail("claim", ["c1"])])
    out = verify_answer(FakeLLM(responses=["garbage not json"]), "q", answer, [])
    assert len(out.details) == 1 and out.unsupported == []


# ── P0.3 parallel branch fan-out ─────────────────────────────────────────────

class _MultiAnalyzer:
    def __init__(self, subqueries):
        self.subqueries = subqueries

    def analyze(self, query, *, base_filter=None):
        return QueryAnalysis(original=query, route="DECOMPOSE",
                             subqueries=self.subqueries, filters=MetaFilter())


def test_parallel_branches_match_sequential(indexed):
    embedder, store, _ = indexed
    analyzer = _MultiAnalyzer(["unemployment stress test scenario",
                               "China semiconductors export controls",
                               "federal funds rate target range"])

    def run(workers):
        r = Retriever(embedder, store, IdentityReranker(), analyzer,
                      RetrievalSettings(final_k=4, chunk_expansion=False,
                                        max_branch_workers=workers))
        chunks, _ = r.retrieve("multi-part macro question")
        return [c.chunk_id for c in chunks]

    assert run(4) == run(1)        # concurrency must not change the result


# ── P0.4 query trace ─────────────────────────────────────────────────────────

def test_build_trace_shape():
    analysis = QueryAnalysis(original="q", route="DIRECT",
                             filters=MetaFilter(factor_tags=["inflation"]))
    chunks = [RetrievedChunk("c1", "t", 0.5, {"source_id": "dfast"}, rerank_score=0.9)]
    answer = Answer(summary="s", details=[AnswerDetail("d", ["c1"])])
    rec = build_trace("q", analysis, chunks, answer,
                      timings={"retrieve": 0.01}, usage={"total_tokens": 42})
    assert rec["route"] == "DIRECT"
    assert rec["filters"]["factor_tags"] == ["inflation"]
    assert rec["retrieved"][0]["rerank_score"] == 0.9
    assert rec["timings_ms"]["retrieve"] == 10.0
    assert rec["usage"]["total_tokens"] == 42
    # final answer is stored on the trace (summary + cited details)
    assert rec["answer"]["summary"] == "s"
    assert rec["answer"]["details"] == [{"text": "d", "chunk_ids": ["c1"]}]


def test_tracer_writes_jsonl(tmp_path):
    path = tmp_path / "tr.jsonl"
    tracer = Tracer(path, enabled=True)
    tracer.write({"a": 1})
    tracer.write({"a": 2})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(ln)["a"] for ln in lines] == [1, 2]


def test_tracer_disabled_writes_nothing(tmp_path):
    path = tmp_path / "tr.jsonl"
    Tracer(path, enabled=False).write({"a": 1})
    assert not path.exists()
