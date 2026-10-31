"""Curation surface tests: vocabulary view + curate job (fake runner)."""

from __future__ import annotations

import time

import pytest

from api.jobs import JobManager  # noqa: E402

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from utils.config import RagSettings  # noqa: E402


def _fake_curation_runner(job):
    job.log(f"running {job.params['task']} dry_run={job.params['dry_run']}")
    return {"task": job.params["task"], "exit_code": 0}


def _client(*, auth=False):
    settings = RagSettings()
    if auth:
        settings.auth.enabled = True
        settings.auth.api_keys = {"admin-key": "t", "user-key": "t2"}
        settings.auth.admin_api_keys = ["admin-key"]
    app = create_app(lambda: None, settings,
                     jobs=JobManager(), curation_runner=_fake_curation_runner)
    return TestClient(app)


def _wait(c, job_id, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        body = c.get(f"/admin/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    return c.get(f"/admin/jobs/{job_id}").json()


# ── vocabulary ────────────────────────────────────────────────────────────────

def test_admin_vocabulary_view():
    c = _client()
    v = c.get("/admin/vocabulary").json()
    assert isinstance(v["factor_tags"], list) and len(v["factor_tags"]) >= 1
    assert isinstance(v["entity_tags"], list)
    assert isinstance(v["aliases"], dict) and len(v["aliases"]) >= 1
    assert isinstance(v["doc_types"], list)


# ── curate job ────────────────────────────────────────────────────────────────

def test_admin_curate_runs_as_job():
    c = _client()
    started = c.post("/admin/curate",
                     json={"task": "normalize-vocab", "dry_run": True}).json()
    assert started["type"] == "curate"
    done = _wait(c, started["id"])
    assert done["status"] == "succeeded"
    assert done["result"] == {"task": "normalize-vocab", "exit_code": 0}
    assert any("normalize-vocab" in e["msg"] for e in done["logs"])


def test_admin_curate_rejects_unknown_task():
    c = _client()
    r = c.post("/admin/curate", json={"task": "drop-tables"})
    assert r.status_code == 400


# ── auth gate ─────────────────────────────────────────────────────────────────

def test_curation_requires_admin():
    c = _client(auth=True)
    assert c.get("/admin/vocabulary").status_code == 401
    assert c.post("/admin/curate", json={"task": "rechunk"},
                  headers={"X-API-Key": "user-key"}).status_code == 403
    ok = c.post("/admin/curate", json={"task": "rechunk"},
                headers={"X-API-Key": "admin-key"})
    assert ok.status_code == 200
