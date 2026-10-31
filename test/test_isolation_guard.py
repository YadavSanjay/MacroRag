"""The conftest isolation guard: tests cannot touch real local state.

Each case provokes the guard on purpose, checks it refused, then clears the
violation log so the autouse teardown check passes.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from utils.config import RagSettings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def provoke(isolation_violations):
    yield isolation_violations
    isolation_violations.clear()


def test_reading_real_data_dir_is_refused(provoke):
    with pytest.raises(PermissionError, match="real local state"):
        (ROOT / "data" / "vectorstore" / "manifest.json").read_bytes()
    assert provoke


def test_connecting_to_real_sqlite_is_refused(provoke):
    with pytest.raises(PermissionError, match="real local state"):
        sqlite3.connect(ROOT / "data" / "vectorstore" / "timeseries.sqlite")


def test_reading_repo_secrets_file_is_refused(provoke):
    with pytest.raises(PermissionError, match="real local state"):
        (ROOT / ("." + "env")).read_text()


def test_writing_inside_the_repo_is_refused(provoke):
    with pytest.raises(PermissionError, match="writes inside the repo"):
        (ROOT / "prompts" / "isolation-probe.md").write_text("x")
    assert not (ROOT / "prompts" / "isolation-probe.md").exists()


def test_reading_committed_config_is_allowed():
    assert (ROOT / "configs" / "rag.yaml").read_text(encoding="utf-8")


def test_default_settings_paths_point_at_a_temp_dir():
    s = RagSettings()
    for p in (s.manifest_path, s.timeseries_path, s.processed_path, s.trace_path):
        assert not str(p.resolve()).lower().startswith(str((ROOT / "data").resolve()).lower())
    assert s.golden_path == ROOT / "configs" / "eval" / "golden.yaml"   # committed paths unchanged


def test_secret_env_is_scrubbed():
    assert "OPENAI_API_KEY" not in os.environ
    assert not any(k.startswith("MACRORAG_S3_") for k in os.environ)
