"""Ingestion surface tests: tier catalog + ingest job (fake runner)."""

from __future__ import annotations

import time

import pytest

from api.jobs import JobManager  # noqa: E402

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from utils.config import RagSettings  # noqa: E402


def _fake_ingest_runner(job):
    job.log(f"ingesting {job.params['tier']}")
    return {"summary": "parsed=3 errors=0", "parsed": 3, "n_errors": 0}


def _client(*, auth=False):
    settings = RagSettings()
    if auth:
        settings.auth.enabled = True
        settings.auth.api_keys = {"admin-key": "t", "user-key": "t2"}
        settings.auth.admin_api_keys = ["admin-key"]
    app = create_app(lambda: None, settings,
                     jobs=JobManager(), ingest_runner=_fake_ingest_runner)
    return TestClient(app)


def _wait(c, job_id, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        body = c.get(f"/admin/jobs/{job_id}").json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.02)
    return c.get(f"/admin/jobs/{job_id}").json()


# ── tier catalog ──────────────────────────────────────────────────────────────

def test_admin_tiers_lists_catalog():
    c = _client()
    tiers = c.get("/admin/tiers").json()["tiers"]
    by_name = {t["name"]: t for t in tiers}
    assert "tier1_fed" in by_name
    fed = by_name["tier1_fed"]
    assert fed["tier"] == 1 and fed["n_endpoints"] >= 1
    assert fed["n_enabled"] <= fed["n_endpoints"]


def test_admin_tier_detail_and_404():
    c = _client()
    t = c.get("/admin/tiers/tier1_fed").json()
    assert t["name"] == "tier1_fed" and t["tier"] == 1
    assert len(t["endpoints"]) >= 1
    ep = t["endpoints"][0]
    assert {"id", "handler", "title", "enabled", "bucket", "discovery"} <= set(ep)
    assert "type" in ep["discovery"] and "url" in ep["discovery"]

    assert c.get("/admin/tiers/tier_nope").status_code == 404


# ── ingest job ────────────────────────────────────────────────────────────────

def test_admin_ingest_runs_as_job():
    c = _client()
    started = c.post("/admin/ingest",
                     json={"tier": "tier1_fed", "endpoints": ["press_monetary"],
                           "limit": 2}).json()
    assert started["type"] == "ingest"
    assert started["params"]["tier"] == "tier1_fed"
    done = _wait(c, started["id"])
    assert done["status"] == "succeeded"
    assert done["result"]["parsed"] == 3


# ── auth gate ─────────────────────────────────────────────────────────────────

def test_ingestion_requires_admin():
    c = _client(auth=True)
    assert c.get("/admin/tiers").status_code == 401
    assert c.post("/admin/ingest", json={"tier": "tier1_fed"},
                  headers={"X-API-Key": "user-key"}).status_code == 403
    ok = c.post("/admin/ingest", json={"tier": "tier1_fed"},
                headers={"X-API-Key": "admin-key"})
    assert ok.status_code == 200
