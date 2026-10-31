"""§L — API hardening + ops: input bounds, admission control, secret-from-file, backup."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from utils.config import read_secret

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from backup_restore import create_backup, restore_backup  # noqa: E402

# ── secret-from-file (Docker/K8s secret mount) ───────────────────────────────

def test_read_secret_prefers_file_then_env(monkeypatch, tmp_path):
    secret_file = tmp_path / "openai.key"
    secret_file.write_text("sk-from-file\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY_FILE", str(secret_file))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert read_secret("OPENAI_API_KEY") == "sk-from-file"   # _FILE wins, whitespace stripped


def test_read_secret_falls_back_to_env(monkeypatch):
    monkeypatch.delenv("FOO_KEY_FILE", raising=False)
    monkeypatch.setenv("FOO_KEY", "plain")
    assert read_secret("FOO_KEY") == "plain"


def test_read_secret_missing_is_none(monkeypatch):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    monkeypatch.delenv("NOPE_KEY_FILE", raising=False)
    assert read_secret("NOPE_KEY") is None


# ── backup / restore roundtrip ───────────────────────────────────────────────

def test_backup_restore_roundtrip(tmp_path):
    root = tmp_path / "data"
    (root / "vectorstore").mkdir(parents=True)
    (root / "vectorstore" / "timeseries.sqlite").write_bytes(b"db-bytes")
    (root / "vectorstore" / "manifest.json").write_text('{"v":1}', encoding="utf-8")
    qdir = root / "vectorstore" / "qdrant"
    qdir.mkdir()
    (qdir / "segment.bin").write_bytes(b"vec")

    archive = tmp_path / "backup.tar.gz"
    items = [root / "vectorstore" / "timeseries.sqlite",
             root / "vectorstore" / "manifest.json", qdir]
    stored = create_backup(items, archive, root=tmp_path)
    assert archive.exists() and len(stored) == 3

    # wipe, then restore
    import shutil
    shutil.rmtree(root)
    restore_backup(archive, tmp_path)
    assert (root / "vectorstore" / "timeseries.sqlite").read_bytes() == b"db-bytes"
    assert (qdir / "segment.bin").read_bytes() == b"vec"


def test_restore_refuses_overwrite_without_force(tmp_path):
    root = tmp_path
    f = root / "manifest.json"
    f.write_text("orig", encoding="utf-8")
    archive = tmp_path / "b.tar.gz"
    create_backup([f], archive, root=root)
    f.write_text("changed", encoding="utf-8")
    with pytest.raises(FileExistsError):
        restore_backup(archive, root)                 # exists -> refuse
    restore_backup(archive, root, force=True)         # force -> overwrite back to "orig"
    assert f.read_text(encoding="utf-8") == "orig"


def test_backup_skips_missing_items(tmp_path):
    archive = tmp_path / "b.tar.gz"
    stored = create_backup([tmp_path / "does-not-exist"], archive, root=tmp_path)
    assert stored == []


# ── API input bounds + admission control (fastapi) ───────────────────────────

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from utils.config import RagSettings  # noqa: E402


def test_oversized_question_is_422():
    c = TestClient(create_app(lambda: object(), RagSettings()))
    r = c.post("/query", json={"question": "x" * 4001})
    assert r.status_code == 422


def test_empty_question_is_422():
    c = TestClient(create_app(lambda: object(), RagSettings()))
    assert c.post("/query", json={"question": ""}).status_code == 422


def test_oversized_filter_list_is_422():
    c = TestClient(create_app(lambda: object(), RagSettings()))
    r = c.post("/query", json={"question": "ok",
                               "filters": {"entity_tags": ["t"] * 101}})
    assert r.status_code == 422


def test_admission_control_returns_503_when_saturated():
    import threading

    entered = threading.Event()        # set once the first request holds the slot
    release = threading.Event()

    class _BlockingPipe:
        def answer(self, q, *, base_filter=None):
            entered.set()
            release.wait(3.0)
            raise RuntimeError("unreached")

    s = RagSettings()
    s.api.max_concurrency = 1          # only one in-flight request allowed
    c = TestClient(create_app(lambda: _BlockingPipe(), s))
    codes: list[int] = []
    t = threading.Thread(target=lambda: codes.append(
        c.post("/query", json={"question": "hold the slot"}).status_code))
    t.start()
    try:
        assert entered.wait(3.0)       # first request is now inside the pipe, holding the slot
        r = c.post("/query", json={"question": "second, should be rejected"})
        assert r.status_code == 503    # no slot left -> admission control rejects
    finally:
        release.set()
        t.join(3.0)
