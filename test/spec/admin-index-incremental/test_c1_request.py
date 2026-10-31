"""C1: POST /admin/index takes append / incremental / dry_run, rejects the two invalid
combinations with 422 (no job), records the flags as job params, and stays exclusive (409)."""

from __future__ import annotations

import threading

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

FLAGS = ("append", "incremental", "dry_run")


def _recording_runner(seen: list):
    def run(job):
        seen.append(dict(job.params))
        return {"ok": True}
    return run


def _expected_accept(body: dict) -> bool:
    # oracle: spec Requirement — dry_run needs incremental; incremental excludes append.
    inc = body.get("incremental", False)
    if body.get("dry_run", False) and not inc:
        return False
    return not (inc and body.get("append", False))


def _full(body: dict) -> dict:
    # oracle: spec Requirement — every flag defaults to false.
    return {f: bool(body.get(f, False)) for f in FLAGS}


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("body", [
    {},
    {"incremental": True},
    {"incremental": True, "dry_run": True},
])
def test_accepted_request_records_all_three_flags(aii, tmp_path, body):
    # oracle: spec C1 — {} -> all false; the incremental requests -> params as given.
    seen: list = []
    client, jobs = aii.make_client(aii.make_settings(tmp_path), _recording_runner(seen))
    resp = client.post("/admin/index", json=body)
    assert resp.status_code == 200, resp.text
    want = {"append": False, "incremental": bool(body.get("incremental")),
            "dry_run": bool(body.get("dry_run"))}
    assert resp.json()["params"] == want
    rec = aii.wait_job(jobs, resp.json()["id"])
    assert rec["status"] == "succeeded", rec
    assert rec["params"] == want
    assert seen == [want]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("body", [
    {"dry_run": True},
    {"incremental": True, "append": True},
])
def test_invalid_combination_is_422_and_submits_nothing(aii, tmp_path, body):
    # oracle: spec C1 — dry_run without incremental, and incremental with append, are 422.
    seen: list = []
    client, jobs = aii.make_client(aii.make_settings(tmp_path), _recording_runner(seen))
    resp = client.post("/admin/index", json=body)
    assert resp.status_code == 422, resp.text
    assert jobs.list() == []
    assert seen == []


@pytest.mark.criterion("C1")
def test_second_request_while_first_runs_is_409(aii, tmp_path):
    # oracle: spec C1 — the index job stays exclusive in the corpus group (409 while running).
    gate = threading.Event()
    seen: list = []

    def blocking(job):
        seen.append(dict(job.params))
        gate.wait(30.0)
        return {}

    client, jobs = aii.make_client(aii.make_settings(tmp_path), blocking)
    try:
        first = client.post("/admin/index", json={"incremental": True})
        assert first.status_code == 200, first.text
        assert first.json()["params"] == {"append": False, "incremental": True,
                                          "dry_run": False}
        second = client.post("/admin/index", json={"incremental": True, "dry_run": True})
        assert second.status_code == 409, second.text
    finally:
        gate.set()
    rec = aii.wait_job(jobs, first.json()["id"])
    assert rec["status"] == "succeeded", rec
    assert len(jobs.list()) == 1


@pytest.mark.criterion("C1")
@settings(max_examples=16)
@given(flags=st.fixed_dictionaries({}, optional={f: st.booleans() for f in FLAGS}))
def test_any_flag_combination_is_accepted_iff_valid(aii, flags):
    # oracle: spec Requirement — acceptance rule computed in _expected_accept; accepted
    # requests carry exactly the three flags (defaults false), rejected ones submit nothing.
    with aii.temp_root() as root, aii.isolated_env():
        seen: list = []
        client, jobs = aii.make_client(aii.make_settings(root), _recording_runner(seen))
        resp = client.post("/admin/index", json=flags)
        if _expected_accept(flags):
            assert resp.status_code == 200, resp.text
            rec = aii.wait_job(jobs, resp.json()["id"])
            assert rec["params"] == _full(flags)
            assert seen == [_full(flags)]
        else:
            assert resp.status_code == 422, resp.text
            assert jobs.list() == [] and seen == []
