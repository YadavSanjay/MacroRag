"""LLM layer: JSON extraction, prompt rendering, planning, answer generation."""

from __future__ import annotations

from llm.base import extract_json
from llm.fake import FakeLLM
from llm.generate import format_context, format_data, generate_answer
from llm.prompts import render
from llm.understanding import LLMAnalyzer
from utils.config import RetrievalSettings
from utils.schema import RetrievedChunk, TimeseriesHit


def test_extract_json_plain_fenced_and_wrapped():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('here you go: {"a": 3} thanks') == {"a": 3}
    assert extract_json("not json at all") == {}


def test_render_substitutes_only_declared_keys():
    out = render("plan", question="will rates fall?")
    assert "will rates fall?" in out
    assert "{question}" not in out


def test_render_answer_keeps_json_braces():
    out = render("answer", question="Q", context="C", data="D")
    assert '"summary"' in out and "Q" in out and "C" in out and "D" in out


def test_llm_analyzer_merges_plan_and_filters():
    fake = FakeLLM(responses=[
        '{"route":"DECOMPOSE","paraphrase":"inflation outlook",'
        '"subqueries":["q1","q2"],"hyde":["h1"]}'])
    qa = LLMAnalyzer(fake, RetrievalSettings()).analyze("inflation in 2026")
    assert qa.route == "DECOMPOSE"
    assert qa.subqueries == ["q1", "q2"] and qa.hyde_docs == ["h1"]
    assert "inflation" in qa.filters.factor_tags
    assert qa.filters.date_from == "2026-01-01"
    # branches = paraphrase + subqueries + hyde
    assert set(qa.search_queries()) == {"inflation outlook", "q1", "q2", "h1"}


def test_llm_analyzer_survives_bad_plan_json():
    qa = LLMAnalyzer(FakeLLM(responses=["not json"]), RetrievalSettings()).analyze("x")
    assert qa.route == "DIRECT"


def test_format_context_and_data():
    chunk = RetrievedChunk(chunk_id="c1", text="body", score=1.0,
                           payload={"publisher": "Fed", "publication": "FOMC",
                                    "release_date": "2026-06-17"})
    ctx = format_context([chunk])
    assert "[c1]" in ctx and "Fed" in ctx and "body" in ctx
    hit = TimeseriesHit(source="DFAST_2026", publisher="Fed",
                        rows=[{"variable": "unemployment_rate", "value": 9.0}])
    assert "DFAST_2026" in format_data([hit])
    assert format_data([]) == "(no numeric series)"


def test_generate_answer_parses_structured_output():
    fake = FakeLLM(responses=[
        '{"summary":"Unemployment peaks at 10%.",'
        '"details":[{"text":"peak 10%","chunk_ids":["dfast_scen::000"]}],'
        '"unanswered":["nothing"]}'])
    chunk = RetrievedChunk(chunk_id="dfast_scen::000", text="...", score=1.0, payload={})
    ans = generate_answer(fake, "unemployment path?", [chunk])
    assert ans.summary.startswith("Unemployment")
    assert ans.used_chunk_ids == ["dfast_scen::000"]
    assert ans.unanswered == ["nothing"]
