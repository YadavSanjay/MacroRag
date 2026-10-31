"""Plan-and-execute agent: calculator safety, typed plan parsing, executor over
the time-series store, and the end-to-end quantitative path."""

from __future__ import annotations

import json

import pytest

from agent.agent import PlanExecuteAgent
from agent.calculator import CalculatorError, evaluate
from agent.executor import PlanExecutor
from agent.planner import LLMPlanner, looks_quantitative, parse_plan
from agent.schema import ComputeStep, DataStep, Plan, RetrieveStep
from llm.fake import FakeLLM
from retriever.rerank import IdentityReranker
from retriever.retriever import Retriever
from utils.config import RagSettings, RetrievalSettings
from utils.trace import Tracer
from vectorstores.timeseries_store import TimeseriesStore

# ── calculator ────────────────────────────────────────────────────────────────

def test_calculator_basic_and_functions():
    assert evaluate("2 + 3 * 4") == 14.0
    assert evaluate("change(a, b)", {"a": 4.5, "b": 10.0}) == 5.5
    assert evaluate("pct_change(a, b)", {"a": 100.0, "b": 110.0}) == 10.0
    assert evaluate("max(s)", {"s": [1, 9, 3]}) == 9.0
    assert evaluate("round(ratio(a, b), 2)", {"a": 1.0, "b": 3.0}) == 0.33


def test_calculator_rejects_unsafe_input():
    for bad in ["__import__('os')", "a.b", "open('x')", "1; 2", "lambda: 1"]:
        with pytest.raises(CalculatorError):
            evaluate(bad, {"a": 1})


def test_calculator_unknown_variable():
    with pytest.raises(CalculatorError):
        evaluate("x + 1")


# ── planner ───────────────────────────────────────────────────────────────────

def test_looks_quantitative_gate():
    assert looks_quantitative("how much did unemployment rise in the stress test?")
    assert not looks_quantitative("summarize the FOMC statement tone")


def test_parse_plan_builds_typed_steps():
    plan = parse_plan({
        "quantitative": True,
        "data": [{"id": "start", "source_like": "DFAST", "variable": "unemployment_rate",
                  "scenario": "severely_adverse", "period": "2026-Q1", "aggregate": "value"},
                 {"id": "peak", "source_like": "DFAST", "variable": "unemployment_rate",
                  "scenario": "severely_adverse", "aggregate": "max"}],
        "compute": [{"id": "rise", "label": "rise", "expression": "change(start, peak)"}],
        "retrieve": [{"id": "ctx", "query": "severely adverse narrative"}],
    })
    assert plan.quantitative
    assert isinstance(plan.data[0], DataStep) and plan.data[1].aggregate == "max"
    assert isinstance(plan.compute[0], ComputeStep)
    assert isinstance(plan.retrieve[0], RetrieveStep)


def test_planner_skips_non_quantitative():
    plan = LLMPlanner(FakeLLM(responses=["{}"])).plan("describe the tone")
    assert plan.quantitative is False        # gate short-circuits, no LLM needed


# ── executor ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ts(tmp_path, corpus_dir):
    from utils.corpus import iter_timeseries_rows
    store = TimeseriesStore(tmp_path / "ts.sqlite")
    store.build(iter_timeseries_rows(corpus_dir))
    return store


def test_executor_data_lookup_and_compute(indexed, ts):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    plan = Plan(
        quantitative=True,
        data=[DataStep(id="start", source_like="DFAST", variable="unemployment_rate",
                       scenario="severely_adverse", period="2026-Q1", aggregate="value"),
              DataStep(id="peak", source_like="DFAST", variable="unemployment_rate",
                       scenario="severely_adverse", aggregate="max")],
        compute=[ComputeStep(id="rise", label="unemployment rise",
                             expression="change(start, peak)")],
    )
    result = PlanExecutor(retriever, ts).execute(plan, "unemployment rise?")
    assert result.values["start"] == 5.9 and result.values["peak"] == 9.0
    assert result.values["rise"] == pytest.approx(3.1)          # deterministic math
    comp = result.computed_hit()
    assert comp and comp.rows[0]["value"] == pytest.approx(3.1)
    assert result.chunks                                        # narrative context too


# ── end-to-end through the pipeline ───────────────────────────────────────────

def test_pipeline_uses_agent_for_quantitative_query(indexed, ts, tmp_path):
    from pipelines.query_pipeline import QueryPipeline
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    plan_json = json.dumps({
        "quantitative": True,
        "data": [{"id": "start", "source_like": "DFAST", "variable": "unemployment_rate",
                  "scenario": "severely_adverse", "period": "2026-Q1", "aggregate": "value"},
                 {"id": "peak", "source_like": "DFAST", "variable": "unemployment_rate",
                  "scenario": "severely_adverse", "aggregate": "max"}],
        "compute": [{"id": "rise", "expression": "change(start, peak)"}],
        "retrieve": [],
    })
    answer_json = '{"summary":"Unemployment rises 3.1ppt to a 10% peak.","details":[],"unanswered":[]}'
    llm = FakeLLM(responses=[plan_json, answer_json])
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    planner = LLMPlanner(llm, sources=ts.sources())
    agent = PlanExecuteAgent(planner, PlanExecutor(retriever, ts))
    pipe = QueryPipeline(retriever, llm, ts, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=None, agent=agent)

    res = pipe.answer("how much does unemployment rise in the severely adverse scenario?")
    assert res.analysis.route == "PLAN_EXECUTE"
    # the deterministic computation reached the generation DATA section
    computed = [h for h in res.answer.timeseries if h.source == "COMPUTED"]
    assert computed and computed[0].rows[0]["value"] == pytest.approx(3.1)
