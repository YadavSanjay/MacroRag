"""C2: through the app with the default index runner, an incremental dry run reports the plan
from run_incremental's stats and writes nothing (no points, no manifest)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

DRY = {"incremental": True, "dry_run": True}


def _run_dry(aii, s) -> dict:
    client, jobs = aii.make_client(s)          # index_runner=None -> the default runner
    resp = client.post("/admin/index", json=DRY)
    assert resp.status_code == 200, resp.text
    return aii.wait_job(jobs, resp.json()["id"])


@pytest.mark.criterion("C2")
def test_dry_run_job_reports_plan_and_writes_nothing(aii, tmp_path):
    # oracle: spec C2 + Notes — two synthetic one-chunk documents on an empty store give
    # scanned 2, new 2, changed 0, unchanged 0, indexed 0, deleted 0, stale 0, dry_run true.
    s, total = aii.build_corpus(tmp_path, [1, 1])
    assert total == 2
    rec = _run_dry(aii, s)
    assert rec["status"] == "succeeded", rec
    result = rec["result"] or {}
    assert result.get("dry_run") is True, result
    assert {k: result.get(k) for k in ("scanned", "new", "changed", "unchanged", "indexed",
                                       "deleted", "stale")} == {
        "scanned": 2, "new": 2, "changed": 0, "unchanged": 0, "indexed": 0, "deleted": 0,
        "stale": 0}
    aii.release_clients()
    assert aii.store_state(s)[0] == 0
    assert not s.manifest_path.exists()
    assert aii.manifest_dir_state(s) == {}


@pytest.mark.metamorphic
@pytest.mark.criterion("C2")
@settings(max_examples=6)
@given(sizes=st.lists(st.integers(min_value=1, max_value=3), min_size=1, max_size=4))
def test_dry_run_scans_every_chunk_whatever_the_corpus(aii, sizes):
    # oracle: spec C2 — on an empty store scanned == new == the chunk count, which the test
    # computes as sum(sizes) from the documents it writes; indexed 0; nothing is written.
    with aii.temp_root() as root, aii.isolated_env():
        s, total = aii.build_corpus(root, sizes)
        rec = _run_dry(aii, s)
        assert rec["status"] == "succeeded", rec
        result = rec["result"] or {}
        assert result.get("dry_run") is True, result
        assert result.get("scanned") == total
        assert result.get("new") == total
        assert result.get("indexed") == 0
        aii.release_clients()
        assert aii.store_state(s)[0] == 0
        assert not s.manifest_path.exists()
