"""End-to-end retriever + query pipeline with fakes (no torch/network)."""

from __future__ import annotations

import json

from llm.fake import FakeLLM
from pipelines.query_pipeline import QueryPipeline
from retriever.rerank import IdentityReranker
from retriever.retriever import Retriever
from utils.config import RagSettings, RetrievalSettings
from utils.schema import MetaFilter
from vectorstores.timeseries_store import TimeseriesStore


def _retriever(indexed, **overrides):
    embedder, store, _ = indexed
    params = {"final_k": 2, "chunk_expansion": True, **overrides}
    return Retriever(embedder, store, IdentityReranker(),
                     settings=RetrievalSettings(**params))


def test_retriever_ranks_relevant_doc_first(indexed):
    r = _retriever(indexed)
    chunks, analysis = r.retrieve("severely adverse unemployment stress test scenario")
    assert chunks[0].payload["source_id"] == "dfast"
    assert analysis.route == "DIRECT"


def test_retriever_applies_extracted_facet_filter(indexed):
    r = _retriever(indexed)
    chunks, _ = r.retrieve("export controls on semiconductors to China")
    assert chunks and chunks[0].payload["source_id"] == "federal_register"
    assert all("china" in c.payload.get("entity_tags", [])
               or c.payload.get("expanded") for c in chunks)


def test_retriever_chunk_expansion_pulls_neighbour(indexed):
    r = _retriever(indexed, final_k=1)
    chunks, _ = r.retrieve("severely adverse unemployment recession peak")
    ids = {c.chunk_id for c in chunks}
    # winner dfast_scen::000 should drag in its neighbour ::001
    assert "dfast_scen::000" in ids and "dfast_scen::001" in ids


def test_retriever_recall_guard_on_overstrict_filter(indexed):
    r = _retriever(indexed)
    # a filter that matches nothing should trigger the unfiltered retry
    chunks, _ = r.retrieve("unemployment",
                           base_filter=MetaFilter(doc_types=["does_not_exist"]))
    assert chunks  # not empty thanks to the recall guard


def test_query_pipeline_end_to_end_with_hydration(indexed, tmp_path, corpus_dir):
    from utils.corpus import iter_timeseries_rows
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))

    ts = TimeseriesStore(tmp_path / "ts.sqlite")
    ts.build(iter_timeseries_rows(corpus_dir))

    answer_json = json.dumps({
        "summary": "Unemployment peaks at 10% in the severely adverse path.",
        "details": [{"text": "peak 10%", "chunk_ids": ["dfast_scen::000"]}],
        "unanswered": [],
    })
    from utils.trace import Tracer
    pipeline = QueryPipeline(retriever, FakeLLM(responses=[answer_json]), ts,
                             RagSettings(), tracer=Tracer(tmp_path / "t.jsonl"))
    result = pipeline.answer("What is the severely adverse unemployment path in the stress test?")

    assert "10%" in result.answer.summary
    assert result.answer.timeseries and result.answer.timeseries[0].source == "DFAST_2026"
    # the numeric series was passed into the generation context
    assert any(r["value"] == 9.0 for r in result.answer.timeseries[0].rows)

    # the written trace captures the final answer AND the per-call LLM log
    line = (tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    rec = json.loads(line)
    assert "10%" in rec["answer"]["summary"]
    assert rec["answer"]["details"][0]["chunk_ids"] == ["dfast_scen::000"]
    usage = rec["usage"]
    assert usage["calls"] >= 1
    gen = next(c for c in usage["call_log"] if c["purpose"] == "generate")
    # prompt (with the retrieved context) and raw response are both captured
    assert "severely adverse" in gen["user"].lower()
    assert "peak 10%" in gen["response"]
