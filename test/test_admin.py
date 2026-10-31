"""Admin/system surface tests (fake pipeline; no models/network).

Covers the read-only /admin/* endpoints that back the UI's technical area:
manifest + staleness, sanitised config + active impls, query traces, and
vector/time-series store stats, plus the admin auth gate.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from llm.fake import FakeLLM  # noqa: E402
from pipelines.query_pipeline import QueryPipeline  # noqa: E402
from retriever.rerank import IdentityReranker  # noqa: E402
from retriever.retriever import Retriever  # noqa: E402
from utils.cache import NullCache  # noqa: E402
from utils.config import RagSettings, RetrievalSettings  # noqa: E402
from utils.manifest import build_manifest, write_manifest  # noqa: E402
from utils.trace import Tracer  # noqa: E402
from vectorstores.timeseries_store import TimeseriesStore  # noqa: E402

_ANSWER = ('{"summary":"Unemployment peaks at 10%.",'
           '"details":[{"text":"peak 10%","chunk_ids":["dfast_scen::000"]}],'
           '"unanswered":[]}')


def _build(indexed, tmp_path, *, auth=False, jobs=None, eval_runner=None):
    """Return (TestClient, settings, points) with all file paths under tmp_path."""
    embedder, store, points = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    settings = RagSettings()
    settings.paths.feedback_log = str(tmp_path / "fb.jsonl")
    settings.paths.manifest = str(tmp_path / "manifest.json")
    settings.paths.timeseries_db = str(tmp_path / "ts.sqlite")
    settings.paths.eval_history = str(tmp_path / "eval_history.jsonl")
    settings.trace.path = str(tmp_path / "queries.jsonl")
    settings.retrieval.verify_faithfulness = False
    settings.inference.remote_url = ""   # force in-process => health probe is deterministic
    if auth:
        settings.auth.enabled = True
        settings.auth.api_keys = {"admin-key": "tenant-a", "user-key": "tenant-b"}
        settings.auth.admin_api_keys = ["admin-key"]
    pipe = QueryPipeline(retriever, FakeLLM(responder=lambda s, u: _ANSWER),
                         None, settings,
                         tracer=Tracer(tmp_path / "queries.jsonl", enabled=False),
                         cache=NullCache())
    app = create_app(lambda: pipe, settings, jobs=jobs, eval_runner=eval_runner)
    return TestClient(app), settings, points


# ── manifest ──────────────────────────────────────────────────────────────────

def test_admin_manifest_missing_then_present(indexed, tmp_path):
    c, settings, _ = _build(indexed, tmp_path)

    r = c.get("/admin/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["manifest"] is None and body["stale"] is True
    assert body["world_view_id"] is None and body["built"] is False
    assert body["pipeline_version"] and body["prompt_hash"]

    # write a current manifest -> not stale, world_view_id derived from built_at
    write_manifest(settings.manifest_path, build_manifest(settings, chunks=4))
    body = c.get("/admin/manifest").json()
    assert body["manifest"]["chunks"] == 4
    assert body["stale"] is False
    assert body["world_view_id"].startswith("macro-")


def test_admin_manifest_history_lists_builds_with_diffs(indexed, tmp_path):
    from utils.manifest import append_manifest_history

    c, settings, _ = _build(indexed, tmp_path)
    target = settings.manifest_path
    append_manifest_history(target, build_manifest(settings, chunks=10, digest="d1"))
    append_manifest_history(target, build_manifest(settings, chunks=12, digest="d2"))

    body = c.get("/admin/manifest/history").json()
    assert body["total"] == 2
    newest, oldest = body["builds"]
    assert newest["corpus_digest"] == "d2" and oldest["corpus_digest"] == "d1"
    # the newest build carries the diff against the one before it
    fields = {ch["field"] for ch in newest["changes"]}
    assert fields == {"corpus_digest", "chunks"}
    assert oldest["changes"] == []          # nothing before it in the window


def test_admin_manifest_history_empty(indexed, tmp_path):
    c, _, _ = _build(indexed, tmp_path)
    body = c.get("/admin/manifest/history").json()
    assert body["total"] == 0 and body["builds"] == []


def test_admin_manifest_verify_known_and_current(indexed, tmp_path):
    from utils.manifest import append_manifest_history, world_view_id_from

    c, settings, _ = _build(indexed, tmp_path)
    man = build_manifest(settings, chunks=4, digest="abc123abc123")
    write_manifest(settings.manifest_path, man)
    append_manifest_history(settings.manifest_path, man)
    wv = world_view_id_from(man)

    body = c.get("/admin/manifest/verify", params={"world_view": wv}).json()
    assert body["known"] is True and body["is_current"] is True
    assert body["build"]["chunks"] == 4

    # a state we have no record of: reported unknown, not rejected outright
    other = c.get("/admin/manifest/verify",
                  params={"world_view": "macro-1999-01-01-000000000000"}).json()
    assert other["known"] is False and other["is_current"] is False


def test_admin_manifest_verify_known_but_superseded(indexed, tmp_path):
    from utils.manifest import append_manifest_history, world_view_id_from

    c, settings, _ = _build(indexed, tmp_path)
    old = build_manifest(settings, chunks=4, digest="aaaaaaaaaaaa")
    new = build_manifest(settings, chunks=9, digest="bbbbbbbbbbbb")
    for man in (old, new):
        append_manifest_history(settings.manifest_path, man)
    write_manifest(settings.manifest_path, new)

    body = c.get("/admin/manifest/verify",
                 params={"world_view": world_view_id_from(old)}).json()
    # the honest audit answer: we know this corpus, but it is no longer live
    assert body["known"] is True and body["is_current"] is False
    assert body["current"] == world_view_id_from(new)


# ── config ────────────────────────────────────────────────────────────────────

def test_admin_config_sanitised_and_impls_after_build(indexed, tmp_path):
    c, _, _ = _build(indexed, tmp_path)

    cfg = c.get("/admin/config").json()
    assert cfg["embedding"]["model"] and cfg["llm"]["answer_model"]
    assert cfg["auth"] == {"enabled": False}      # never the keys
    assert "api_keys" not in json.dumps(cfg)
    assert cfg["built"] is False and cfg["active_impls"] is None

    # building the pipeline (via a query) reveals the concrete impls
    c.post("/query", json={"question": "severely adverse unemployment"})
    cfg = c.get("/admin/config").json()
    assert cfg["built"] is True
    impls = cfg["active_impls"]
    assert impls["vector_store"] == "InMemoryVectorStore"
    assert impls["reranker"] == "IdentityReranker"
    assert impls["llm"] == "FakeLLM"
    assert impls["embedder"] == "HashingEmbedder"


# ── traces ────────────────────────────────────────────────────────────────────

def test_admin_traces_pagination_and_single(indexed, tmp_path):
    c, settings, _ = _build(indexed, tmp_path)
    # seed three trace records oldest->newest
    with open(settings.trace_path, "w", encoding="utf-8") as fh:
        for i in range(3):
            fh.write(json.dumps({"query": f"q{i}", "route": "DIRECT"}) + "\n")

    body = c.get("/admin/traces", params={"limit": 2}).json()
    assert body["total"] == 3 and body["limit"] == 2
    # most-recent-first, with running index
    assert [t["query"] for t in body["traces"]] == ["q2", "q1"]
    assert [t["i"] for t in body["traces"]] == [0, 1]

    # offset paging
    body2 = c.get("/admin/traces", params={"limit": 2, "offset": 2}).json()
    assert [t["query"] for t in body2["traces"]] == ["q0"]

    # single by index (0 = most recent)
    assert c.get("/admin/traces/0").json()["query"] == "q2"
    assert c.get("/admin/traces/99").status_code == 404


def test_admin_traces_empty_when_no_file(indexed, tmp_path):
    c, _, _ = _build(indexed, tmp_path)
    body = c.get("/admin/traces").json()
    assert body["total"] == 0 and body["traces"] == []


def test_admin_traces_list_strips_call_bodies_detail_keeps_them(indexed, tmp_path):
    c, settings, _ = _build(indexed, tmp_path)
    rec = {
        "query": "q", "route": "DIRECT",
        "answer": {"summary": "s", "details": []},
        "usage": {
            "calls": 1, "total_tokens": 30, "cost_usd": 0.001, "cost_complete": True,
            "call_log": [{
                "purpose": "generate", "model": "gpt-5.4", "prompt_tokens": 20,
                "completion_tokens": 10, "cost_usd": 0.001,
                "system": "SYS", "user": "USER-PROMPT", "response": "RAW-RESPONSE",
            }],
        },
    }
    with open(settings.trace_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")

    # list view: token/cost accounting kept, prompt/response bodies dropped
    call = c.get("/admin/traces").json()["traces"][0]["usage"]["call_log"][0]
    assert call["prompt_tokens"] == 20 and call["cost_usd"] == 0.001
    assert "system" not in call and "user" not in call and "response" not in call

    # detail view: full bodies present
    dcall = c.get("/admin/traces/0").json()["usage"]["call_log"][0]
    assert dcall["user"] == "USER-PROMPT" and dcall["response"] == "RAW-RESPONSE"


# ── stores ────────────────────────────────────────────────────────────────────

def test_admin_store_vector_count(indexed, tmp_path):
    c, settings, points = _build(indexed, tmp_path)
    body = c.get("/admin/stores/vector").json()
    assert body["backend"] == "InMemoryVectorStore"
    assert body["count"] == len(points)
    assert body["collection"] == settings.qdrant.collection


def test_admin_store_timeseries_absent_then_present(indexed, tmp_path):
    c, settings, _ = _build(indexed, tmp_path)

    body = c.get("/admin/stores/timeseries").json()
    assert body["available"] is False and body["count"] == 0

    # build a tiny series store at the configured path
    TimeseriesStore(settings.timeseries_path).build([
        {"source": "DFAST_2026", "variable": "unemployment_rate", "value": 9.0},
        {"source": "SEP_2026-06", "variable": "gdp_growth", "value": 1.2},
    ])
    body = c.get("/admin/stores/timeseries").json()
    assert body["available"] is True and body["count"] == 2
    assert body["sources"] == ["DFAST_2026", "SEP_2026-06"]


# ── store probes (Tier 3) ───────────────────────────────────────────────────────

def test_admin_vector_probe(indexed, tmp_path):
    c, _, points = _build(indexed, tmp_path)
    body = c.post("/admin/stores/vector/search",
                  json={"query": "unemployment path", "limit": 3}).json()
    assert body["query"] == "unemployment path"
    assert 1 <= body["n"] <= 3
    cand = body["candidates"][0]
    # pre-rerank candidate carries a fused score, provenance brief and a text snippet
    assert "chunk_id" in cand and "score" in cand and "snippet" in cand
    assert cand["chunk_id"] in {p.chunk_id for p in points}


def test_admin_timeseries_query(indexed, tmp_path):
    c, settings, _ = _build(indexed, tmp_path)
    # absent store -> available False, no rows
    assert c.get("/admin/stores/timeseries/query").json() == {
        "available": False, "n": 0, "rows": []}

    TimeseriesStore(settings.timeseries_path).build([
        {"source": "DFAST_2026", "variable": "unemployment_rate", "scenario": "sa", "value": 9.0},
        {"source": "DFAST_2026", "variable": "gdp_growth", "scenario": "sa", "value": -2.0},
        {"source": "SEP_2026-06", "variable": "gdp_growth", "value": 1.2},
    ])
    all_rows = c.get("/admin/stores/timeseries/query").json()
    assert all_rows["available"] is True and all_rows["n"] == 3
    assert "raw" not in all_rows["rows"][0]      # heavy blob trimmed

    # filter by source + variable
    one = c.get("/admin/stores/timeseries/query",
                params={"source": "DFAST_2026", "variable": "gdp_growth"}).json()
    assert one["n"] == 1 and one["rows"][0]["value"] == -2.0


# ── health probes (Tier 2) ──────────────────────────────────────────────────────

def test_admin_health_probes_lazy_then_built(indexed, tmp_path):
    c, _, _ = _build(indexed, tmp_path)

    body = c.get("/admin/health").json()
    assert {ch["name"] for ch in body["checks"]} == {
        "vector_store", "timeseries_store", "inference_service", "llm_provider"}
    by = {ch["name"]: ch for ch in body["checks"]}
    # not built yet: vector store probed lazily; ts store absent; inference disabled
    assert by["vector_store"]["status"] == "skipped"
    assert by["timeseries_store"]["status"] == "down"
    assert by["inference_service"]["status"] == "skipped"
    assert body["built"] is False
    # a down time-series store (non-critical) => degraded, not down
    assert body["status"] == "degraded"
    assert all("latency_ms" in ch for ch in body["checks"])

    # a query builds the pipeline: the vector store now reports a live count
    c.post("/query", json={"question": "severely adverse unemployment"})
    body = c.get("/admin/health").json()
    by = {ch["name"]: ch for ch in body["checks"]}
    assert by["vector_store"]["status"] == "ok"
    assert body["built"] is True


# ── metrics roll-up (Tier 2) ────────────────────────────────────────────────────

def test_admin_metrics_aggregates_traces(indexed, tmp_path):
    c, settings, _ = _build(indexed, tmp_path)
    recs = [
        {"ts": "2026-07-01T00:00:00Z", "route": "DIRECT", "refused": False,
         "cached": False, "timings_ms": {"retrieve": 100, "generate": 200},
         "usage": {"cost_usd": 0.01, "cost_complete": True, "total_tokens": 30,
                   "prompt_tokens": 20, "completion_tokens": 10,
                   "call_log": [{"model": "gpt-5.4", "total_tokens": 30, "cost_usd": 0.01}]}},
        {"ts": "2026-07-01T01:00:00Z", "route": "DIRECT", "refused": True,
         "cached": False, "timings_ms": {"retrieve": 50},
         "usage": {"cost_usd": 0.005, "cost_complete": True, "total_tokens": 10,
                   "call_log": [{"model": "gpt-5.4-nano", "total_tokens": 10, "cost_usd": 0.005}]}},
        {"ts": "2026-07-02T00:00:00Z", "route": "HYDE", "refused": False,
         "cached": True, "timings_ms": {"retrieve": 80, "generate": 120},
         "usage": {"cost_usd": 0.02, "cost_complete": True, "total_tokens": 40,
                   "call_log": [{"model": "gpt-5.4", "total_tokens": 40, "cost_usd": 0.02}]}},
    ]
    with open(settings.trace_path, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")

    m = c.get("/admin/metrics").json()
    assert m["n_traces"] == 3
    assert m["queries"]["refused"] == 1 and m["queries"]["cached"] == 1
    assert m["queries"]["refusal_rate"] == round(1 / 3, 4)
    assert abs(m["cost"]["total_usd"] - 0.035) < 1e-9
    assert m["cost"]["total_tokens"] == 80
    # latency per trace = sum(timings): 300, 50, 200 -> avg 183.3
    assert m["latency_ms"]["avg"] == round((300 + 50 + 200) / 3, 1)
    routes = {r["route"]: r for r in m["by_route"]}
    assert routes["DIRECT"]["n"] == 2 and routes["DIRECT"]["refused"] == 1
    models = {mm["model"]: mm for mm in m["by_model"]}
    assert models["gpt-5.4"]["calls"] == 2 and models["gpt-5.4"]["total_tokens"] == 70
    assert [d["date"] for d in m["daily"]] == ["2026-07-01", "2026-07-02"]


def test_admin_metrics_empty(indexed, tmp_path):
    c, _, _ = _build(indexed, tmp_path)
    m = c.get("/admin/metrics").json()
    assert m["n_traces"] == 0
    assert m["queries"]["refusal_rate"] == 0.0 and m["latency_ms"]["p95"] == 0.0


# ── eval history (Tier 2) ───────────────────────────────────────────────────────

def test_admin_eval_history(indexed, tmp_path):
    c, settings, _ = _build(indexed, tmp_path)
    assert c.get("/admin/eval/history").json() == {"total": 0, "runs": []}

    with open(settings.eval_history_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-07-01T00:00:00Z", "k": 8,
                             "summary": {"mrr": 0.5}}) + "\n")
        fh.write(json.dumps({"ts": "2026-07-02T00:00:00Z", "k": 8,
                             "summary": {"mrr": 0.7}}) + "\n")

    body = c.get("/admin/eval/history").json()
    assert body["total"] == 2
    assert body["runs"][0]["summary"]["mrr"] == 0.7   # most recent first


# ── job cancel (Tier 3 wrap-up) ─────────────────────────────────────────────────

def test_admin_job_cancel(indexed, tmp_path):
    import threading
    import time

    from api.jobs import JobManager

    jm = JobManager()
    started = threading.Event()

    # a runner that loops on the cooperative cancel checkpoint
    def eval_runner(job):
        started.set()
        while True:
            job.check_cancel()
            time.sleep(0.005)

    c, _, _ = _build(indexed, tmp_path, jobs=jm, eval_runner=eval_runner)

    assert c.post("/admin/jobs/nope/cancel").status_code == 404

    job = c.post("/admin/eval", json={"k": 8}).json()
    assert started.wait(2.0)                       # runner is actually running

    r = c.post(f"/admin/jobs/{job['id']}/cancel").json()
    assert r["id"] == job["id"]

    # the job stops cooperatively and lands in CANCELLED (not failed)
    status = None
    for _ in range(400):
        status = c.get(f"/admin/jobs/{job['id']}").json()["status"]
        if status in ("cancelled", "failed", "succeeded"):
            break
        time.sleep(0.01)
    assert status == "cancelled"

    # cancelling a terminal job is idempotent (still 200, unchanged status)
    again = c.post(f"/admin/jobs/{job['id']}/cancel").json()
    assert again["status"] == "cancelled"


# ── auth gate ─────────────────────────────────────────────────────────────────

def test_admin_auth_requires_admin_key(indexed, tmp_path):
    c, _, _ = _build(indexed, tmp_path, auth=True)
    assert c.get("/admin/manifest").status_code == 401                      # no key
    assert c.get("/admin/manifest",
                 headers={"X-API-Key": "bad"}).status_code == 401           # invalid
    assert c.get("/admin/manifest",
                 headers={"X-API-Key": "user-key"}).status_code == 403      # not admin
    assert c.get("/admin/manifest",
                 headers={"X-API-Key": "admin-key"}).status_code == 200     # admin
