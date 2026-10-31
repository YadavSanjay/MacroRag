"""Fixtures for the admin-index-incremental spec tests.

Turns the repo dotenv file off before anything from ``src`` is imported and puts ``src`` on
``sys.path``.

Every test runs against temporary directories only (spec Notes): a processed corpus of
synthetic one-or-more-chunk documents, a local (file-mode) Qdrant store, a time-series SQLite
file and a manifest, all under a fresh temporary root. Nothing reads ``data/``.

No torch and no network. ``RAGINFER_URL`` is set for every test to a base URL whose scheme no
HTTP client speaks (``NO_NETWORK_URL``) and ``inference.fallback_local`` is off, so:

- an embedder that is merely *built* is a ``RemoteEmbedder`` (construction does no I/O) rather
  than the in-process bge-m3 model an empty ``RAGINFER_URL`` would load;
- an embedder that is *used* fails at once with an unsupported-protocol error, before any
  socket is opened, and never falls back to a local model.

This keeps today's code (which ignores ``incremental`` and ``should_cancel``) failing by
assertion rather than by loading models, and a wrong implementation equally safe.

Qdrant's local mode holds a file lock on its path while a client is open, so every helper
closes the client it opens, and ``release_clients`` collects clients left behind by code under
test before the store is reopened.
"""

from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

# Before any import from src: never load the repo's secrets dotenv file.
os.environ["MACRORAG_DOTENV"] = "0"

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.config import RagSettings  # noqa: E402  (needs src on sys.path, set just above)

# A base URL no HTTP client can reach: an unknown scheme fails before any connection.
NO_NETWORK_URL = "spec-no-network://admin-index-incremental"
JOB_TIMEOUT_S = 60.0       # generous bound on a background job (local Qdrant is slow to open)
MANIFEST_TEXT = '{"spec": "pre-existing manifest, must survive a cancelled refresh"}'


def _env(mp: pytest.MonkeyPatch) -> None:
    mp.setenv("RAGINFER_URL", NO_NETWORK_URL)
    mp.setenv("RAGINFER_FALLBACK_LOCAL", "0")
    for name in ("MACRORAG_DEPLOY_MODE", "MACRORAG_QDRANT_URL", "MACRORAG_REDIS_URL"):
        mp.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_network_inference(monkeypatch):
    """Per test: inference points at the no-network URL; no deploy/Qdrant/Redis overrides."""
    _env(monkeypatch)
    yield


@contextmanager
def isolated_env():
    """The same environment as the autouse fixture, usable inside a hypothesis example."""
    with pytest.MonkeyPatch.context() as mp:
        _env(mp)
        yield


# ── synthetic corpus ─────────────────────────────────────────────────────────────

def write_doc(processed: Path, doc_id: str, n_chunks: int, version: str = "v1") -> None:
    """One synthetic processed document with ``n_chunks`` chunks (shape as in
    ``test/test_incremental_index.py``), no time series."""
    content_hash = f"{doc_id}-{version}"
    chunks = [{
        "chunk_id": f"{doc_id}::{i:03d}", "seq": i,
        "text": f"Synthetic note {doc_id} part {i}: policy rates and credit spreads.",
        "metadata": {"content_hash": content_hash, "tier": 1, "bucket": "B",
                     "publisher": "Synthetic Bank", "publication": "Synthetic Review",
                     "release_date": "2026-06-01"},
    } for i in range(n_chunks)]
    doc = {
        "doc_id": doc_id, "source_id": "synthetic", "title": doc_id, "doc_type": "speech",
        "publisher": "Synthetic Bank", "publication": "Synthetic Review",
        "release_date": "2026-06-01", "retrieval_date": "2026-06-19",
        "url": f"https://example.invalid/{doc_id}", "content_hash": content_hash,
        "tier": 1, "bucket": "B", "chunks": chunks, "timeseries": [],
    }
    folder = processed / "tier1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}.json").write_text(json.dumps(doc), encoding="utf-8")


def make_settings(root: Path) -> RagSettings:
    """RagSettings over temporary paths only (spec Notes recipe)."""
    s = RagSettings(qdrant={"url": None, "path": str(root / "qdrant")})
    s.deploy_mode = "local"
    s.inference.fallback_local = False
    s.paths.processed_dir = str(root / "processed")
    s.paths.manifest = str(root / "vectorstore" / "manifest.json")
    s.paths.timeseries_db = str(root / "timeseries.sqlite")
    s.paths.feedback_log = str(root / "traces" / "feedback.jsonl")
    s.paths.eval_baseline = str(root / "eval" / "baseline.json")
    s.paths.eval_history = str(root / "eval" / "history.jsonl")
    s.paths.golden_candidates = str(root / "eval" / "golden_candidates.yaml")
    s.trace.path = str(root / "traces" / "trace.jsonl")
    (root / "processed").mkdir(parents=True, exist_ok=True)
    return s


def build_corpus(root: Path, sizes: list[int]) -> tuple[RagSettings, int]:
    """Settings plus a corpus of ``len(sizes)`` documents with the given chunk counts.
    Returns (settings, total chunks) — the total is the sum, counted here."""
    s = make_settings(root)
    for k, n in enumerate(sizes):
        write_doc(Path(s.paths.processed_dir), f"doc{k}", n)
    return s, sum(sizes)


# ── Qdrant store helpers ─────────────────────────────────────────────────────────

def release_clients() -> None:
    """Drop Qdrant local clients left unreferenced by code under test (file lock)."""
    gc.collect()


def _open_store(s: RagSettings):
    from vectorstores import build_qdrant_store

    q = s.qdrant.model_copy()
    q.path = str(s.qdrant_path())
    return build_qdrant_store(q, dense_dim=s.embedding.dense_dim)


def _close(store) -> None:
    store.client.close()


def preindex(s: RagSettings, doc_ids: list[str] | None = None) -> int:
    """Index the corpus (or only ``doc_ids``) with the hashing test embedder, so the store
    holds those chunks with their current signatures. Returns the number upserted."""
    from embeddings.hashing import HashingEmbedder
    from utils.corpus import iter_chunk_points
    from utils.objectstore import corpus_store

    store = _open_store(s)
    try:
        store.ensure_collection(recreate=False)
        points = [p for p in iter_chunk_points(corpus_store(s),
                                               use_grounding=s.embedding.use_grounding_prefix)
                  if doc_ids is None or p.doc_id in doc_ids]
        if points:
            emb = HashingEmbedder(dense_dim=s.embedding.dense_dim)
            store.upsert(points, emb.embed_documents([p.embed_text for p in points]))
        return len(points)
    finally:
        _close(store)


def store_state(s: RagSettings) -> tuple[int, dict[str, str]]:
    """(point count, chunk_id -> stored signature); an absent collection reads as empty."""
    release_clients()
    store = _open_store(s)
    try:
        if not store.client.collection_exists(s.qdrant.collection):
            return 0, {}
        return store.count(), dict(store.existing_signatures())
    finally:
        _close(store)


def manifest_dir_state(s: RagSettings) -> dict[str, str]:
    """Every file beside the manifest (manifest + history log) with its text."""
    d = s.manifest_path.parent
    if not d.exists():
        return {}
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.iterdir()) if p.is_file()}


def put_manifest(s: RagSettings) -> None:
    s.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    s.manifest_path.write_text(MANIFEST_TEXT, encoding="utf-8")


# ── app helpers ──────────────────────────────────────────────────────────────────

def make_client(s: RagSettings, index_runner=None):
    """(TestClient, JobManager) for ``create_app`` with the given (or default) runner."""
    from fastapi.testclient import TestClient

    from api.app import create_app
    from api.jobs import JobManager

    jobs = JobManager()
    app = create_app(lambda: None, s, jobs=jobs, index_runner=index_runner)
    return TestClient(app), jobs


def wait_job(jobs, job_id: str) -> dict:
    """The job's final record; a job still running after the bound is an assertion failure."""
    job = jobs.get(job_id)
    assert job is not None, f"job {job_id} not found"
    assert job.wait(JOB_TIMEOUT_S), f"job {job_id} did not finish: {job.to_dict()}"
    return job.to_dict()


@contextmanager
def temp_root():
    """A fresh temporary root for one hypothesis example (Windows may keep Qdrant files
    briefly locked, so cleanup errors are ignored)."""
    with tempfile.TemporaryDirectory(prefix="aii-", ignore_cleanup_errors=True) as d:
        yield Path(d)
    release_clients()


@pytest.fixture(scope="session")
def aii():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        NO_NETWORK_URL=NO_NETWORK_URL, MANIFEST_TEXT=MANIFEST_TEXT, isolated_env=isolated_env,
        write_doc=write_doc, make_settings=make_settings, build_corpus=build_corpus,
        release_clients=release_clients, preindex=preindex, store_state=store_state,
        manifest_dir_state=manifest_dir_state, put_manifest=put_manifest,
        make_client=make_client, wait_job=wait_job, temp_root=temp_root)
