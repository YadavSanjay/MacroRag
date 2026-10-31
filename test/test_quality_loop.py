"""§B — quality & evaluation loop: schema-health, judge→eval, feedback→golden.

All fakes: scripted FakeLLM, in-memory store, no network.
"""

from __future__ import annotations

import json

import pytest

from eval.feedback_reconcile import reconcile_feedback, write_candidates
from llm.fake import FakeLLM
from llm.schema_health import capture_schema, reset_schema_health, schema_health_snapshot

_SCHEMA = {"type": "object", "required": ["x"]}


# ── B1: schema-health recorder ───────────────────────────────────────────────

def _run_once(responder):
    reset_schema_health()
    FakeLLM(responder=responder).complete_json(
        system="s", user="u", schema=_SCHEMA, model="m", purpose="mytask")
    return schema_health_snapshot()["by_task"]["mytask"]["m"]


def test_schema_ok_outcome():
    snap = _run_once(lambda s, u: '{"x": 1}')
    assert snap["ok"] == 1 and snap["repaired"] == 0 and snap["failed"] == 0
    assert snap["repair_rate"] == 0.0


def test_schema_repaired_outcome():
    # first reply invalid; the repair retry (its user carries the repair template) fixes it
    snap = _run_once(lambda s, u: '{"x": 1}' if "did not satisfy" in u else '{"z": 1}')
    assert snap["repaired"] == 1 and snap["ok"] == 0
    assert snap["repair_rate"] == 1.0


def test_schema_failed_outcome():
    snap = _run_once(lambda s, u: '{"z": 1}')     # never valid, even after repair
    assert snap["failed"] == 1 and snap["ok"] == 0


def test_capture_schema_scopes_repair_rate():
    reset_schema_health()
    llm = FakeLLM(responder=lambda s, u: '{"x": 1}')
    with capture_schema() as sc:
        llm.complete_json(system="s", user="u", schema=_SCHEMA, model="m", purpose="t")
        llm.complete_json(system="s", user="u", schema=_SCHEMA, model="m", purpose="t")
    assert sc.total == 2 and sc.repair_rate() == 0.0


def test_schema_health_feeds_metrics():
    reset_schema_health()
    FakeLLM(responder=lambda s, u: '{"x": 1}').complete_json(
        system="s", user="u", schema=_SCHEMA, model="gpt-x", purpose="gen")
    from utils.metrics import render_metrics
    assert 'macrorag_schema_results_total{task="gen",model="gpt-x",outcome="ok"}' in render_metrics()


# ── B3: feedback → golden candidates ─────────────────────────────────────────

def _write_feedback(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def _write_golden(path, questions):
    import yaml
    path.write_text(yaml.safe_dump({"questions": questions}), encoding="utf-8")


def test_reconcile_proposes_from_positive_cited_feedback(tmp_path):
    fb = tmp_path / "feedback.jsonl"
    golden = tmp_path / "golden.yaml"
    _write_feedback(fb, [
        {"question": "Q new", "rating": 1, "chunk_ids": ["c::1"], "ts": "t1"},
        {"question": "Q negative", "rating": -1, "chunk_ids": ["c::2"]},   # downvote → skip
        {"question": "Q no chunks", "rating": 1, "chunk_ids": []},          # no chunks → skip
        {"question": "Q existing", "rating": 1, "chunk_ids": ["c::3"]},     # already golden → skip
        {"question": "Q new", "rating": 1, "chunk_ids": ["c::9"]},          # dup question → skip
    ])
    _write_golden(golden, [{"question": "Q existing", "match_field": "source_id",
                            "expected": ["x"]}])
    cands = reconcile_feedback(fb, golden)
    assert [c["question"] for c in cands] == ["Q new"]
    c = cands[0]
    assert c["match_field"] == "chunk_id" and c["expected"] == ["c::1"]
    assert c["source"] == "feedback"


def test_write_candidates_roundtrips(tmp_path):
    import yaml
    out = tmp_path / "cands.yaml"
    write_candidates([{"question": "Q", "match_field": "chunk_id", "expected": ["a"]}], out)
    loaded = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert loaded["questions"][0]["question"] == "Q"


def test_reconcile_empty_when_no_feedback(tmp_path):
    assert reconcile_feedback(tmp_path / "missing.jsonl", tmp_path / "g.yaml") == []


# ── B2 + endpoints (fastapi) ─────────────────────────────────────────────────

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from pipelines.query_pipeline import QueryPipeline  # noqa: E402
from retriever.rerank import IdentityReranker  # noqa: E402
from retriever.retriever import Retriever  # noqa: E402
from utils.cache import NullCache  # noqa: E402
from utils.config import RagSettings, RetrievalSettings  # noqa: E402
from utils.trace import Tracer  # noqa: E402

# One JSON reply that satisfies BOTH the answer schema and the judge schema
# (permissive schemas require only their primary key), so a single responder drives
# generate_answer AND judge_answer in evaluate_answers.
_UNION = ('{"summary":"S","details":[{"text":"d","chunk_ids":["dfast_scen::000"]}],'
          '"unanswered":[],"faithfulness":0.9,"relevancy":0.8,"reason":"ok"}')


def _pipe(indexed, tmp_path):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    return QueryPipeline(retriever, FakeLLM(responder=lambda s, u: _UNION),
                         None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache())


def test_evaluate_answers_aggregates_judge_scores(indexed, tmp_path):
    from eval.harness import evaluate_answers
    pipe = _pipe(indexed, tmp_path)
    r = evaluate_answers(pipe, [{"question": "severely adverse unemployment"}])
    assert r["summary"]["faithfulness"] == pytest.approx(0.9)
    assert r["summary"]["relevancy"] == pytest.approx(0.8)
    assert r["rows"][0]["reason"] == "ok"


def test_admin_schema_health_endpoint():
    app = create_app(lambda: object(), RagSettings())
    c = TestClient(app)
    body = c.get("/admin/schema-health").json()
    assert "by_task" in body and "totals" in body


def test_admin_feedback_reconcile_endpoint(tmp_path):
    settings = RagSettings()
    settings.paths.feedback_log = str(tmp_path / "fb.jsonl")
    settings.paths.golden = str(tmp_path / "golden.yaml")
    settings.paths.golden_candidates = str(tmp_path / "cands.yaml")
    _write_feedback(tmp_path / "fb.jsonl",
                    [{"question": "Q up", "rating": 1, "chunk_ids": ["c::1"]}])
    (tmp_path / "golden.yaml").write_text("questions: []\n", encoding="utf-8")
    c = TestClient(create_app(lambda: object(), settings))
    r = c.post("/admin/feedback/reconcile")
    assert r.status_code == 200
    body = r.json()
    assert body["n_proposed"] == 1 and body["candidates"][0]["question"] == "Q up"
