"""Shared pytest fixtures: src on path + a small synthetic corpus indexed into
the in-memory store with the deterministic hashing embedder (no torch/network)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

# Tests never see the developer's secrets: src's config modules load the repo-root
# dotenv file at import unless MACRORAG_DOTENV=0, so set it before anything under
# src is imported. Subprocesses inherit it.
os.environ["MACRORAG_DOTENV"] = "0"

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ── isolation guard ──────────────────────────────────────────────────────────
# Any test that opens the real data/ tree or the repo-root secrets file, or writes
# anywhere in the repo outside the tool caches below, fails. Opt out for local
# debugging with MACRORAG_TEST_GUARD=0.
_GUARD_ON = os.environ.get("MACRORAG_TEST_GUARD", "1") != "0"
_REPO = os.path.normcase(str(ROOT.resolve()))
_DATA = os.path.join(_REPO, "data")
_SECRETS = os.path.join(_REPO, "." + "env")
_WRITE_OK = tuple(os.path.join(_REPO, d) for d in (
    ".hypothesis", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".coverage"))
_VIOLATIONS: list[str] = []


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _is_write(mode: object, flags: object) -> bool:
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(
        flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND))


def _isolation_hook(event: str, args: tuple) -> None:
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
    elif event == "sqlite3.connect":
        path, mode, flags = (args[0] if args else None), "r+", None
    else:
        return
    if path is None or isinstance(path, int):
        return
    try:
        p = os.path.normcase(os.path.abspath(os.fsdecode(path)))
    except (TypeError, ValueError):
        return
    if not _under(p, _REPO):
        return
    if _under(p, _DATA) or p == _SECRETS:
        reason = "reads/writes real local state"
    elif (_is_write(mode, flags) and "__pycache__" not in p
          and not any(_under(p, ok) for ok in _WRITE_OK)):
        reason = "writes inside the repo"
    else:
        return
    msg = f"test isolation: {reason}: {p}"
    _VIOLATIONS.append(msg)
    raise PermissionError(msg)


if _GUARD_ON:
    sys.addaudithook(_isolation_hook)   # an audit hook cannot be removed; it is session-wide


# Environment a developer shell may carry that would change behaviour or expose a
# live credential: secrets (and their *_FILE variants) and deployment overrides.
_ENV_SCRUB = re.compile(
    r"^(?:.*(?:API_KEY|_TOKEN|_SECRET|PASSWORD|_KEY_ID)(?:_FILE)?|FINRA_.*|IEA_.*|USITC_.*"
    r"|MACRORAG_(?!TEST_GUARD$|DOTENV$).*|RAGINFER_.*|HF_HOME|AWS_PROFILE)$")


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path_factory, monkeypatch):
    """Point every default ``data/`` path, the log root and the cwd at a per-test
    temp dir, scrub secrets and deployment env, and fail the test on any guard
    violation."""
    from utils import logging as app_logging
    from utils.config import Paths

    root = tmp_path_factory.mktemp("isolated")
    real_resolve = Paths.resolve

    def resolve(self, value: str) -> Path:
        p = Path(value)
        if not p.is_absolute() and p.parts[:1] == ("data",):
            return root / p
        return real_resolve(self, value)

    monkeypatch.setattr(Paths, "resolve", resolve)
    monkeypatch.setattr(app_logging, "LOG_ROOT", root)
    # The MCP SDK's FastMCP settings (pydantic-settings) parse a dotenv file from the
    # cwd; running each test from its temp dir keeps them from reading the repo's.
    monkeypatch.chdir(root)
    for name in list(os.environ):
        if _ENV_SCRUB.match(name):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MACRORAG_LOG_DIR", str(root))     # for subprocesses
    del _VIOLATIONS[:]
    yield
    if _VIOLATIONS:
        found, _VIOLATIONS[:] = list(_VIOLATIONS), []
        pytest.fail("\n".join(dict.fromkeys(found)), pytrace=False)


@pytest.fixture
def isolation_violations() -> list[str]:
    """The guard's violation log for this test; a test that provokes the guard on
    purpose clears it so the teardown check passes."""
    return _VIOLATIONS


@pytest.fixture
def prompts_copy(tmp_path, monkeypatch) -> Path:
    """A temp copy of ``prompts/`` that ``utils.manifest.prompt_hash`` reads instead."""
    from utils import manifest

    dst = tmp_path / "prompts"
    shutil.copytree(manifest._PROMPT_DIR, dst)
    monkeypatch.setattr(manifest, "_PROMPT_DIR", dst)
    return dst


def _doc(doc_id, source_id, doc_type, tier, factor_tags, entity_tags,
         publisher, publication, release_date, chunk_texts, timeseries=None):
    chunks = []
    for i, text in enumerate(chunk_texts):
        chunks.append({
            "chunk_id": f"{doc_id}::{i:03d}",
            "seq": i,
            "text": text,
            "metadata": {
                "publisher": publisher, "publication": publication,
                "release_date": release_date, "retrieval_date": "2026-06-19",
                "url": f"https://example/{doc_id}", "content_hash": doc_id,
                "licence": "public_domain_us_gov", "tier": tier, "bucket": "B",
                "doc_type": doc_type, "section": None,
                "entity_tags": entity_tags, "factor_tags": factor_tags,
                "trades_affected": [],
            },
        })
    return {
        "doc_id": doc_id, "source_id": source_id, "title": publication,
        "doc_type": doc_type, "publisher": publisher, "publication": publication,
        "release_date": release_date, "retrieval_date": "2026-06-19",
        "url": f"https://example/{doc_id}", "content_hash": doc_id,
        "tier": tier, "bucket": "B",
        "chunks": chunks, "timeseries": timeseries or [],
    }


@pytest.fixture
def corpus_dir(tmp_path) -> Path:
    d = tmp_path / "processed"
    (d / "tier1").mkdir(parents=True)
    docs = [
        _doc("dfast_scen", "dfast", "dfast_scenarios", 1,
             ["discount_rate", "equity_vol"], [],
             "Federal Reserve", "Dodd-Frank Act Stress Test", "2026-02-04",
             ["In the severely adverse scenario the unemployment rate rises sharply "
              "to a peak of 10 percent as the economy enters a deep recession.",
              "Real GDP declines and the BBB corporate yield and market volatility "
              "index spike under the severely adverse supervisory stress test."]),
        _doc("fomc_stmt", "press_monetary", "fomc_statement", 1,
             ["discount_rate"], [],
             "Federal Reserve", "FOMC Statement", "2026-06-17",
             ["The Committee decided to maintain the target range for the federal "
              "funds rate at 4-1/4 to 4-1/2 percent."]),
        _doc("bis_rule", "federal_register", "bis_export_rule", 6,
             ["export_controls", "china_taiwan_tail"], ["semiconductors", "china"],
             "U.S. Federal Register", "BIS Export Rule", "2026-05-13",
             ["The Bureau of Industry and Security imposes new export controls on "
              "advanced semiconductors and AI chips destined for China."]),
        _doc("dfast_data", "dfast", "dfast_scenario_data", 1, [], [],
             "Federal Reserve", "DFAST scenario data", None, [],
             timeseries=[
                 {"source": "DFAST_2026", "scenario": "severely_adverse",
                  "domain": "domestic", "variable": "unemployment_rate",
                  "period": f"2026-Q{q}", "value": v}
                 for q, v in [(1, 5.9), (2, 7.2), (3, 8.2), (4, 9.0)]
             ]),
    ]
    for doc in docs:
        (d / "tier1" / f"{doc['doc_id']}.json").write_text(
            json.dumps(doc), encoding="utf-8")
    return d


@pytest.fixture
def indexed(corpus_dir):
    from embeddings.hashing import HashingEmbedder
    from utils.corpus import iter_chunk_points
    from vectorstores.memory import InMemoryVectorStore

    embedder = HashingEmbedder(dense_dim=64)
    store = InMemoryVectorStore()
    points = list(iter_chunk_points(corpus_dir))
    store.upsert(points, embedder.embed_documents([p.embed_text for p in points]))
    return embedder, store, points
