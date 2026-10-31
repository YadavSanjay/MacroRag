"""Quality surface tests: golden set, eval job, drift baseline, feedback inbox."""

from __future__ import annotations

import time

import pytest

from api.jobs import JobManager  # noqa: E402

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from utils.config import RagSettings  # noqa: E402


def _fake_eval_runner(job):
    k = int(job.params.get("k", 8))
    job.log(f"evaluating @k={k}")
    return {
        "k": k,
        "summary": {f"hit@{k}": 1.0, f"recall@{k}": 0.5, "mrr": 0.75},
        "rows": [{"question": "q", "gold": ["dfast"], "top": ["dfast"],
                  f"hit@{k}": 1.0, "mrr": 1.0}],
        "baseline": None,
        "regressions": [],
    }


def _fake_selfcheck_runner(job):
    job.log(f"generating cases (per_group={job.params.get('per_group')})")
    return {
        "summary": {"n_cases": 4, "numeric_agreement": 0.75, "hydration_rate": 1.0,
                    "statement_rate": 0.75, "refusal_rate": 0.0,
                    "both_miss_rate": 0.0, "n_probes": 3,
                    "fabrication_rate": 0.0, "abstention_rate": 1.0,
                    "refused_rate": 1.0, "corrected_rate": 0.0,
                    "evasive_rate": 0.0},
        "numeric": {"summary": {}, "rows": []},
        "probes": {"summary": {}, "rows": []},
    }


def _fake_backtest_runner(job):
    modes = job.params.get("modes") or ["grounding_fidelity", "ex_ante_skill"]
    job.log(f"episodes={job.params.get('episodes')}")
    return {
        "summary": {
            "grounding_fidelity_sign_accuracy": 1.0,
            "grounding_fidelity_tolerance_rate": 0.75,
            "grounding_fidelity_abstention_rate": 0.0,
            "ex_ante_skill_sign_accuracy": 0.5,
            "ex_ante_skill_tolerance_rate": 0.25,
            "ex_ante_skill_abstention_rate": 0.8,
        },
        "modes": {m: {"summary": {}, "rows": []} for m in modes},
        "episodes": [],
    }


def _client(tmp_path, *, auth=False):
    settings = RagSettings()
    settings.paths.feedback_log = str(tmp_path / "fb.jsonl")
    settings.paths.eval_baseline = str(tmp_path / "baseline.json")
    settings.paths.timeseries_db = str(tmp_path / "ts.sqlite")
    if auth:
        settings.auth.enabled = True
        settings.auth.api_keys = {"admin-key": "t", "user-key": "t2"}
        settings.auth.admin_api_keys = ["admin-key"]
    app = create_app(lambda: None, settings,
                     jobs=JobManager(), eval_runner=_fake_eval_runner,
                     selfcheck_runner=_fake_selfcheck_runner,
                     backtest_runner=_fake_backtest_runner)
    return TestClient(app), settings


def _wait(c, job_id, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        body = c.get(f"/admin/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    return c.get(f"/admin/jobs/{job_id}").json()


# ── golden set ────────────────────────────────────────────────────────────────

def test_admin_golden_lists_questions(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/admin/golden").json()
    assert body["path"].endswith("golden.yaml")
    assert isinstance(body["questions"], list) and len(body["questions"]) >= 1
    assert "question" in body["questions"][0]


# ── eval job ──────────────────────────────────────────────────────────────────

def test_admin_eval_runs_as_job(tmp_path):
    c, _ = _client(tmp_path)
    started = c.post("/admin/eval", json={"k": 5}).json()
    assert started["type"] == "eval"
    done = _wait(c, started["id"])
    assert done["status"] == "succeeded"
    assert done["result"]["summary"]["hit@5"] == 1.0
    assert done["result"]["k"] == 5


# ── drift baseline ────────────────────────────────────────────────────────────

def test_admin_eval_baseline_roundtrip(tmp_path):
    c, _ = _client(tmp_path)
    assert c.get("/admin/eval/baseline").json()["baseline"] is None

    summary = {"hit@8": 0.85, "mrr": 0.6}
    saved = c.post("/admin/eval/baseline", json={"summary": summary})
    assert saved.status_code == 200 and saved.json()["baseline"] == summary
    assert c.get("/admin/eval/baseline").json()["baseline"] == summary


# ── feedback inbox ────────────────────────────────────────────────────────────

def test_admin_feedback_lists_recent_first(tmp_path):
    c, settings = _client(tmp_path)
    # write feedback via the public endpoint (no auth) to exercise the real format
    for i in range(3):
        c.post("/feedback", json={"question": f"q{i}", "rating": 1 if i % 2 else -1})

    body = c.get("/admin/feedback", params={"limit": 2}).json()
    assert body["total"] == 3 and len(body["items"]) == 2
    assert [it["question"] for it in body["items"]] == ["q2", "q1"]   # recent first
    assert body["items"][0]["i"] == 0

    page2 = c.get("/admin/feedback", params={"limit": 2, "offset": 2}).json()
    assert [it["question"] for it in page2["items"]] == ["q0"]


def test_admin_feedback_empty(tmp_path):
    c, _ = _client(tmp_path)
    body = c.get("/admin/feedback").json()
    assert body["total"] == 0 and body["items"] == []


# ── self-check job ────────────────────────────────────────────────────────────

def test_admin_selfcheck_runs_and_returns_both_loops(tmp_path):
    c, _ = _client(tmp_path)
    job = c.post("/admin/selfcheck", json={"per_group": 1, "per_kind": 2}).json()
    body = _wait(c, job["id"])
    assert body["status"] == "succeeded"
    summary = body["result"]["summary"]
    assert summary["n_cases"] == 4 and summary["n_probes"] == 3
    assert summary["fabrication_rate"] == 0.0
    assert set(body["result"]) == {"summary", "numeric", "probes"}


def test_admin_selfcheck_defaults_are_applied(tmp_path):
    c, _ = _client(tmp_path)
    job = c.post("/admin/selfcheck", json={}).json()
    body = _wait(c, job["id"])
    assert body["status"] == "succeeded"
    assert body["params"]["per_group"] == 2 and body["params"]["max_cases"] == 100
    assert any("per_group=2" in entry["msg"] for entry in body.get("logs") or [])


# ── realized-episode backtest ─────────────────────────────────────────────────

def test_admin_backtest_keeps_the_two_modes_separate(tmp_path):
    c, _ = _client(tmp_path)
    job = c.post("/admin/backtest", json={"episodes": ["gfc_2008"]}).json()
    body = _wait(c, job["id"])
    assert body["status"] == "succeeded"
    s = body["result"]["summary"]
    # every metric is mode-prefixed; nothing merged
    assert s["grounding_fidelity_sign_accuracy"] == 1.0
    assert s["ex_ante_skill_sign_accuracy"] == 0.5
    assert "sign_accuracy" not in s
    assert any("gfc_2008" in e["msg"] for e in body.get("logs") or [])


def test_admin_backtest_mode_selection_is_passed_through(tmp_path):
    c, _ = _client(tmp_path)
    job = c.post("/admin/backtest",
                 json={"modes": ["grounding_fidelity"]}).json()
    body = _wait(c, job["id"])
    assert list(body["result"]["modes"]) == ["grounding_fidelity"]


def test_admin_backtest_episodes_catalog_without_a_store(tmp_path):
    # no time-series db on disk: the catalog still lists episodes, with no
    # reference rather than an invented one
    c, _ = _client(tmp_path)
    body = c.get("/admin/backtest/episodes").json()
    assert body["total"] >= 5
    first = body["episodes"][0]
    assert first["key"] and first["pit_cutoff"] and first["reference"] == {}


def test_admin_backtest_episodes_catalog_derives_from_the_store(tmp_path):
    from vectorstores.timeseries_store import TimeseriesStore

    c, settings = _client(tmp_path)
    rows = [{"source": "DFAST_2026", "scenario": "historic", "variable": "vix",
             "period": p, "value": v, "publisher": "frb", "tier": 1}
            for p, v in (("2007-Q3", 20.0), ("2007-Q4", 20.0), ("2008-Q1", 20.0),
                         ("2008-Q2", 20.0), ("2008-Q3", 30.0), ("2008-Q4", 70.0))]
    TimeseriesStore(settings.timeseries_path).build(rows)

    body = c.get("/admin/backtest/episodes").json()
    gfc = next(e for e in body["episodes"] if e["key"] == "gfc_2008")
    assert gfc["fields"] == ["VIX"]
    assert gfc["reference"]["VIX"]["value"] == 50.0
    assert gfc["pit_cutoff"] == "2008-06-30"


# ── auth gate ─────────────────────────────────────────────────────────────────

def test_quality_endpoints_require_admin(tmp_path):
    c, _ = _client(tmp_path, auth=True)
    assert c.get("/admin/golden").status_code == 401
    assert c.get("/admin/feedback", headers={"X-API-Key": "user-key"}).status_code == 403
    assert c.post("/admin/eval", json={},
                  headers={"X-API-Key": "admin-key"}).status_code == 200
    assert c.post("/admin/selfcheck", json={}).status_code == 401
    assert c.post("/admin/selfcheck", json={},
                  headers={"X-API-Key": "user-key"}).status_code == 403
    assert c.post("/admin/backtest", json={}).status_code == 401
    assert c.get("/admin/backtest/episodes",
                 headers={"X-API-Key": "user-key"}).status_code == 403
