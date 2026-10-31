"""Indexing pipeline: processed ingest JSON -> embeddings -> vector store, plus
the numeric time-series SQLite store. Components are injected so it works with
the production (bge-m3 + Qdrant) or test (hashing + in-memory) stack.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Optional

from embeddings.base import Embedder
from utils.config import RagSettings, load_settings
from utils.corpus import iter_chunk_points, iter_timeseries_rows
from utils.logging import get_logger
from utils.objectstore import corpus_store, manifest_store
from vectorstores.base import VectorStore
from vectorstores.timeseries_store import TimeseriesStore

log = get_logger(__name__)


def index_chunks(embedder: Embedder, store: VectorStore, settings: RagSettings,
                 *, batch_size: int = 128, recreate: bool = True,
                 should_cancel: Callable[[], None] | None = None) -> int:
    """Embed and upsert every chunk point under the processed dir.

    ``should_cancel`` (optional) is checked at each batch boundary so a running
    index build can be stopped cooperatively between batches."""
    store.ensure_collection(recreate=recreate)
    total, batch = 0, []
    for point in iter_chunk_points(corpus_store(settings),
                                   use_grounding=settings.embedding.use_grounding_prefix):
        batch.append(point)
        if len(batch) >= batch_size:
            if should_cancel is not None:
                should_cancel()
            total += _flush(embedder, store, batch)
            batch = []
    if batch:
        total += _flush(embedder, store, batch)
    if hasattr(store, "finalize_index"):
        store.finalize_index()
    log.info("indexed %d chunks into the vector store", total)
    return total


def _flush(embedder: Embedder, store: VectorStore, batch: list) -> int:
    embeddings = embedder.embed_documents([p.embed_text for p in batch])
    return store.upsert(batch, embeddings)


def _signature(point) -> str:
    """Change signature for a chunk point: the ingest per-doc ``content_hash``
    (stable across re-fetches of unchanged content) if present, else a hash of
    the grounded embed text. Compared against the stored signature to decide
    whether a point needs re-embedding."""
    h = point.payload.get("content_hash")
    if h:
        return str(h)
    return hashlib.sha256(point.embed_text.encode("utf-8")).hexdigest()


def index_chunks_incremental(embedder: Embedder | None, store: VectorStore,
                             settings: RagSettings, *, batch_size: int = 128,
                             dry_run: bool = False,
                             should_cancel: Callable[[], None] | None = None) -> dict:
    """Embed + upsert only the chunks that are **new or changed** vs what the
    store already holds, and delete chunks that have left the corpus — instead of
    re-embedding everything. New/changed is decided by comparing each processed
    chunk's :func:`_signature` against the stored ``content_hash``
    (``store.existing_signatures``). Returns a stats dict; with ``dry_run`` it
    reports the plan without embedding, upserting, or deleting. ``should_cancel``
    (when given) is called before each embedding batch is written and before the
    departed chunks are deleted; whatever it raises stops the refresh there."""
    store.ensure_collection(recreate=False)          # ensure exists; never drop
    existing = store.existing_signatures()           # chunk_id -> content_hash
    stats = {"scanned": 0, "new": 0, "changed": 0, "unchanged": 0,
             "indexed": 0, "deleted": 0, "dry_run": dry_run}
    current_ids: set[str] = set()
    batch: list = []

    def flush():
        if not batch:
            return
        if not dry_run:
            if should_cancel is not None:
                should_cancel()
            stats["indexed"] += _flush(embedder, store, batch)
        batch.clear()

    for point in iter_chunk_points(corpus_store(settings),
                                   use_grounding=settings.embedding.use_grounding_prefix):
        stats["scanned"] += 1
        current_ids.add(point.chunk_id)
        old = existing.get(point.chunk_id)
        if old is None:
            stats["new"] += 1
        elif old != _signature(point):
            stats["changed"] += 1
        else:
            stats["unchanged"] += 1
            continue
        batch.append(point)
        if len(batch) >= batch_size:
            flush()
    flush()

    stale = sorted(set(existing) - current_ids)
    stats["stale"] = len(stale)
    if not dry_run:
        if stale and should_cancel is not None:
            should_cancel()
        stats["deleted"] = store.delete_by_chunk_ids(stale) if stale else 0
        if stats["indexed"] and hasattr(store, "finalize_index"):
            store.finalize_index()                   # rebuild HNSW only if we wrote
    log.info("incremental index: %s", stats)
    return stats


def build_timeseries(settings: RagSettings) -> int:
    store = TimeseriesStore(settings.timeseries_path)
    return store.build(iter_timeseries_rows(corpus_store(settings)), reset=True)


def _corpus_digest(store: VectorStore) -> str | None:
    """Content-address the indexed corpus from the store's own signatures, so the
    manifest's ``world_view_id`` identifies content rather than a build date.
    Returns None (legacy date-only id) if the store can't enumerate them."""
    from utils.manifest import corpus_digest
    try:
        return corpus_digest(store.existing_signatures())
    except Exception as exc:  # noqa: BLE001 - a digest is never worth failing a build
        log.warning("corpus digest unavailable: %s", exc)
        return None


def _finish_manifest(settings: RagSettings, store: VectorStore, *,
                     chunks: int, observations: int) -> dict:
    """Write the manifest for a completed build and record it in the history log."""
    from utils.manifest import append_manifest_history, build_manifest, write_manifest
    target = manifest_store(settings)
    manifest = build_manifest(settings, chunks=chunks, observations=observations,
                              digest=_corpus_digest(store))
    write_manifest(target, manifest)
    append_manifest_history(target, manifest)
    return manifest


def run(settings: RagSettings | None = None, *, recreate: bool = True,
        should_cancel: Callable[[], None] | None = None) -> dict:
    """Full index: vector store (bge-m3 + Qdrant) + time-series store."""
    from embeddings import select_embedder
    from vectorstores import build_qdrant_store

    settings = settings or load_settings()
    embedder = select_embedder(settings)            # remote (RagInference) when configured
    qsettings = settings.qdrant.model_copy()
    if settings.qdrant_path():                      # pin the on-disk path absolute
        qsettings.path = str(settings.qdrant_path())
    store = build_qdrant_store(qsettings, dense_dim=settings.embedding.dense_dim)
    n_chunks = index_chunks(embedder, store, settings, recreate=recreate,
                            should_cancel=should_cancel)
    n_obs = build_timeseries(settings)
    manifest = _finish_manifest(settings, store, chunks=n_chunks,
                                observations=n_obs)
    return {"chunks": n_chunks, "observations": n_obs,
            "corpus_digest": manifest.get("corpus_digest")}


def _build_store(settings: RagSettings):
    from vectorstores import build_qdrant_store
    qsettings = settings.qdrant.model_copy()
    if settings.qdrant_path():                      # pin the on-disk path absolute
        qsettings.path = str(settings.qdrant_path())
    return build_qdrant_store(qsettings, dense_dim=settings.embedding.dense_dim)


def run_incremental(settings: RagSettings | None = None, *,
                    dry_run: bool = False,
                    should_cancel: Callable[[], None] | None = None) -> dict:
    """Incremental refresh: embed + upsert only new/changed chunks, delete
    departed ones (never a full re-embed). Rebuilds the cheap SQLite time-series
    store and the manifest on a real (non-dry) run. ``dry_run`` reports the plan
    without loading the embedder or writing anything. ``should_cancel`` (the admin
    job's cancel check) is called before the embedder is built or anything is
    written, between embedding batches, and before the time-series store and
    manifest are rebuilt (spec admin-index-incremental)."""
    from embeddings import select_embedder

    settings = settings or load_settings()
    store = _build_store(settings)
    if should_cancel is not None:
        should_cancel()
    embedder = None if dry_run else select_embedder(settings)   # remote when configured
    stats = index_chunks_incremental(embedder, store, settings, dry_run=dry_run,
                                     should_cancel=should_cancel)
    if dry_run:
        return stats
    if should_cancel is not None:
        should_cancel()
    n_obs = build_timeseries(settings)
    n_chunks = store.count()
    manifest = _finish_manifest(settings, store, chunks=n_chunks,
                                observations=n_obs)
    stats["observations"] = n_obs
    stats["chunks_total"] = n_chunks
    stats["corpus_digest"] = manifest.get("corpus_digest")
    return stats
