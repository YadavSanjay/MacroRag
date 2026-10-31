"""Job manager + admin job/index endpoint tests (fake runner; no models)."""

from __future__ import annotations

import threading
import time

import pytest

from api.jobs import JobConflict, JobManager  # noqa: E402

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from utils.config import RagSettings  # noqa: E402

# ── JobManager unit ─────────────────────────────────────────────────────────

def test_job_manager_runs_and_records_result():
    mgr = JobManager()
    job = mgr.submit("demo", {"x": 1}, lambda j: (j.log("hi"), {"ok": True})[1])
    assert job.wait(2.0)
    assert job.status == "succeeded"
    assert job.result == {"ok": True}
    assert any(e["msg"] == "hi" for e in job.to_dict()["logs"])


def test_job_manager_captures_failure():
    mgr = JobManager()

    def boom(_job):
        raise ValueError("boom")

    job = mgr.submit("demo", {}, boom)
    assert job.wait(2.0)
    assert job.status == "failed" and job.error == "boom"


# ── exclusive-group guard (§H: no concurrent corpus writers) ─────────────────

def test_exclusive_group_blocks_concurrent_then_frees():
    mgr = JobManager()
    gate = threading.Event()
    j1 = mgr.submit("index", {}, lambda _j: gate.wait(2.0) and {}, exclusive_group="corpus")
    # a second job in the same group while j1 runs -> rejected
    with pytest.raises(JobConflict):
        mgr.submit("index", {}, lambda _j: {}, exclusive_group="corpus")
    gate.set()
    assert j1.wait(2.0) and j1.status == "succeeded"
    # group is free again once j1 is terminal
    j2 = mgr.submit("index", {}, lambda _j: {}, exclusive_group="corpus")
    assert j2.wait(2.0) and j2.status == "succeeded"


def test_non_exclusive_jobs_run_concurrently():
    mgr = JobManager()
    gate = threading.Event()
    j1 = mgr.submit("demo", {}, lambda _j: gate.wait(2.0) and {})  # no group
    j2 = mgr.submit("demo", {}, lambda _j: {})                      # not blocked
    assert j2.wait(2.0) and j2.status == "succeeded"
    gate.set()
    assert j1.wait(2.0)


def test_admin_index_409_on_concurrent_build(tmp_path):
    gate = threading.Event()

    def blocking_runner(job):
        gate.wait(3.0)
        return {}

    settings = RagSettings()
    settings.paths.manifest = str(tmp_path / "manifest.json")
    app = create_app(lambda: None, settings,
                     jobs=JobManager(), index_runner=blocking_runner)
    c = TestClient(app)
    try:
        first = c.post("/admin/index", json={"append": False})
        assert first.status_code == 200
        second = c.post("/admin/index", json={"append": False})
        assert second.status_code == 409
        assert "already running" in second.json()["detail"]
    finally:
        gate.set()


# ── admin index/job endpoints ────────────────────────────────────────────────

def _fake_index_runner(job):
    job.log("embedding chunks")
    job.log("writing manifest")
    return {"chunks": 4, "observations": 4}


def _client(tmp_path, *, auth=False):
    settings = RagSettings()
    settings.paths.manifest = str(tmp_path / "manifest.json")
    if auth:
        settings.auth.enabled = True
        settings.auth.api_keys = {"admin-key": "t", "user-key": "t2"}
        settings.auth.admin_api_keys = ["admin-key"]
    app = create_app(lambda: None, settings,
                     jobs=JobManager(), index_runner=_fake_index_runner)
    return TestClient(app)


def _wait(c, job_id, timeout=3.0, headers=None):
    end = time.time() + timeout
    while time.time() < end:
        body = c.get(f"/admin/jobs/{job_id}", headers=headers).json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    return c.get(f"/admin/jobs/{job_id}", headers=headers).json()


def test_admin_index_submits_job_and_completes(tmp_path):
    c = _client(tmp_path)
    started = c.post("/admin/index", json={"append": False}).json()
    # a fast (fake) runner may already be terminal by the time the POST returns
    assert started["type"] == "index"
    assert started["status"] in ("queued", "running", "succeeded")
    job_id = started["id"]

    done = _wait(c, job_id)
    assert done["status"] == "succeeded"
    assert done["result"] == {"chunks": 4, "observations": 4}
    assert [e["msg"] for e in done["logs"]] == ["embedding chunks", "writing manifest"]

    listing = c.get("/admin/jobs").json()["jobs"]
    assert any(j["id"] == job_id for j in listing)
    assert "logs" not in listing[0]            # list view is summary-only


def test_admin_job_404(tmp_path):
    c = _client(tmp_path)
    assert c.get("/admin/jobs/nope").status_code == 404
    assert c.get("/admin/jobs/nope/stream").status_code == 404


def test_admin_job_stream_emits_logs_and_done(tmp_path):
    c = _client(tmp_path)
    job_id = c.post("/admin/index", json={}).json()["id"]
    _wait(c, job_id)
    r = c.get(f"/admin/jobs/{job_id}/stream")
    assert r.status_code == 200
    assert "event: log" in r.text
    assert "embedding chunks" in r.text
    assert "event: status" in r.text and "succeeded" in r.text
    assert "event: done" in r.text


def test_admin_index_requires_admin_when_auth_on(tmp_path):
    c = _client(tmp_path, auth=True)
    assert c.post("/admin/index", json={}).status_code == 401              # no key
    assert c.post("/admin/index", json={},
                  headers={"X-API-Key": "user-key"}).status_code == 403    # not admin
    ok = c.post("/admin/index", json={}, headers={"X-API-Key": "admin-key"})
    assert ok.status_code == 200
