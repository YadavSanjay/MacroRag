"""Index manifest (ARag review §2.1) — records what produced the current index
so re-embedding is safe and detectable. Captures pipeline version, embedding
model/dim/grounding, reranker + LLM models, a hash of all prompt templates, and
counts. ``is_stale`` flags when the embedding-relevant signature changed (model,
dim, grounding, or pipeline version) — i.e. the vector store must be rebuilt.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Optional

from .config import REPO_ROOT, RagSettings
from .logging import get_logger
from .objectstore import LocalObjectStore, ObjectStore, manifest_store

log = get_logger(__name__)

# A manifest target: an (ObjectStore, key) pair (from ``manifest_store(settings)``),
# a bare ObjectStore, or a local path (back-compat).
ManifestTarget = "tuple[ObjectStore, str] | ObjectStore | Path | str"


def _resolve_manifest(target) -> tuple[ObjectStore, str]:
    if isinstance(target, tuple):
        return target
    if isinstance(target, ObjectStore):
        return target, "manifest.json"
    p = Path(target)
    return LocalObjectStore(p.parent), p.name

PIPELINE_VERSION = "1.0.0"   # bump on any change that requires a full re-embed
# The versioned prompt registry (src/llm/registry.py resolves against this):
#   prompts/system/<role>.md
#   prompts/tasks/<task>/{base.md, base.meta.yaml, schema.json}
_PROMPT_DIR = REPO_ROOT / "prompts"
_PROMPT_SUFFIXES = (".md", ".yaml", ".yml", ".json")


def prompt_hash() -> str:
    """Content hash of the whole prompt registry — the manifest's prompt-drift
    signal. Walks ``prompts/`` recursively (the registry is nested per task) and
    folds in each file's repo-relative path plus its bytes, ordered
    deterministically so the digest is stable across platforms."""
    h = hashlib.sha256()
    for f in sorted(p for p in _PROMPT_DIR.rglob("*")
                    if p.is_file() and p.suffix.lower() in _PROMPT_SUFFIXES):
        h.update(f.relative_to(_PROMPT_DIR).as_posix().encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def corpus_digest(signatures) -> str:
    """Content-address the corpus state behind an index.

    A stable digest over the sorted ``(chunk_id, content_hash)`` pairs — the same
    map incremental indexing already diffs against
    (``VectorStore.existing_signatures``). Accepts a mapping or an iterable of
    pairs.

    This is what makes ``world_view_id`` an *identity* rather than a timestamp:
    two different corpora indexed on the same day used to collide silently, and
    an unchanged corpus re-indexed produced a new id for no reason. Both are
    fixed by hashing content instead of the clock."""
    pairs = (signatures.items() if hasattr(signatures, "items") else signatures)
    h = hashlib.sha256()
    for chunk_id, content_hash in sorted(pairs):
        h.update(str(chunk_id).encode("utf-8"))
        h.update(b"\x00")
        h.update(str(content_hash or "").encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def build_manifest(settings: RagSettings, *, chunks: int = 0,
                   observations: int = 0, digest: str | None = None) -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "built_at": datetime.now(UTC).isoformat(),
        "embedding": {
            "model": settings.embedding.model,
            "dense_dim": settings.embedding.dense_dim,
            "grounding_prefix": settings.embedding.use_grounding_prefix,
        },
        "reranker": settings.reranker.model,
        "llm": {"plan": settings.llm.plan_model, "answer": settings.llm.answer_model,
                "complex": settings.llm.complex_model},
        "prompt_hash": prompt_hash(),
        "qdrant_collection": settings.qdrant.collection,
        "chunks": chunks,
        "observations": observations,
        # None on a store that cannot enumerate signatures; world_view_id then
        # falls back to the legacy date-only form.
        "corpus_digest": digest,
    }


def embedding_signature(manifest: dict) -> tuple:
    """The fields that, if changed, require rebuilding the vector index."""
    e = manifest.get("embedding", {})
    return (manifest.get("pipeline_version"), e.get("model"),
            e.get("dense_dim"), e.get("grounding_prefix"))


def write_manifest(target, manifest: dict) -> None:
    """Persist the manifest. ``target`` is an ``(ObjectStore, key)`` pair (from
    ``manifest_store(settings)``), a bare ObjectStore, or a local path (back-compat
    — byte-identical to the old direct write)."""
    store, key = _resolve_manifest(target)
    store.put_text(key, json.dumps(manifest, indent=2))


def read_manifest(target) -> dict | None:
    store, key = _resolve_manifest(target)
    text = store.get_text(key)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:  # pragma: no cover
        return None


def is_stale(settings: RagSettings) -> bool:
    """True if no manifest exists or the embedding signature has drifted."""
    stored = read_manifest(manifest_store(settings))
    if stored is None:
        return True
    return embedding_signature(stored) != embedding_signature(build_manifest(settings))


def world_view_id_from(manifest: dict | None) -> str | None:
    """``macro-<YYYY-MM-DD>-<corpus digest>`` for a manifest record.

    The date prefix is unchanged from spec §5.5, so consumers that parse the
    build date out of the id keep working; the digest suffix is what makes two
    same-day corpora distinguishable. Manifests written before digests existed
    have no ``corpus_digest`` and keep the legacy date-only form rather than
    being retroactively (and falsely) given an identity."""
    if not manifest:
        return None
    built = str(manifest.get("built_at", ""))[:10]
    if not built:
        return None
    digest = manifest.get("corpus_digest")
    return f"macro-{built}-{digest}" if digest else f"macro-{built}"


def world_view_id(settings: RagSettings) -> str | None:
    """Stable id for the corpus state behind a synthesis response (spec §5.5)."""
    return world_view_id_from(read_manifest(manifest_store(settings)))


# ── build history (append-only) ──────────────────────────────────────────────
# A record per index build, so a past result's world_view_id can be checked
# against a corpus state we actually know about, and two states can be diffed.
# This is an audit *trail*, not a snapshot store: it proves what a corpus was,
# it does not let you retrieve against a superseded one.

def history_target(target) -> tuple[ObjectStore, str]:
    """The history log sitting beside a manifest target (``manifest.json`` ->
    ``manifest_history.jsonl``)."""
    store, key = _resolve_manifest(target)
    stem = key.rsplit(".", 1)[0] if "." in key else key
    return store, f"{stem}_history.jsonl"


def read_manifest_history(target, *, limit: int | None = None) -> list[dict]:
    """Recorded builds, most recent first."""
    store, key = history_target(target)
    text = store.get_text(key)
    if not text:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:                        # pragma: no cover
            continue
    out.reverse()
    return out[:limit] if limit else out


def append_manifest_history(target, manifest: dict, *,
                            max_entries: int = 250) -> None:
    """Append one build to the history log (oldest trimmed past ``max_entries``).

    Best-effort: the log is an audit aid, so a write failure must never fail the
    index build that produced the manifest. ObjectStore has no append (S3), so
    this is read-modify-write — fine for a log of this size."""
    try:
        store, key = history_target(target)
        entry = {"world_view_id": world_view_id_from(manifest), **manifest}
        existing = store.get_text(key) or ""
        lines = [ln for ln in existing.splitlines() if ln.strip()]
        lines.append(json.dumps(entry, ensure_ascii=False, default=str))
        store.put_text(key, "\n".join(lines[-max_entries:]) + "\n")
    except Exception as exc:  # noqa: BLE001 - never fail a build over the log
        log.warning("manifest history write failed: %s", exc)


def find_build(target, world_view: str) -> dict | None:
    """The recorded build for a ``world_view_id``, or None if we have no record
    of that corpus state — the question an auditor actually asks about an old
    result ("was this ever our corpus, and what was in it?")."""
    for entry in read_manifest_history(target):
        if entry.get("world_view_id") == world_view:
            return entry
    return None


_DIFF_FIELDS = ("corpus_digest", "chunks", "observations", "prompt_hash",
                "pipeline_version", "reranker", "qdrant_collection")


def diff_manifests(before: dict, after: dict) -> list[dict]:
    """Field-level changes between two builds, for "what moved between W1 and
    W2". Reports the embedding block as one unit because a change to any part of
    it invalidates the whole index."""
    changes: list[dict] = []
    for field in _DIFF_FIELDS:
        old, new = before.get(field), after.get(field)
        if old != new:
            changes.append({"field": field, "before": old, "after": new})
    if before.get("embedding") != after.get("embedding"):
        changes.append({"field": "embedding", "before": before.get("embedding"),
                        "after": after.get("embedding")})
    if before.get("llm") != after.get("llm"):
        changes.append({"field": "llm", "before": before.get("llm"),
                        "after": after.get("llm")})
    return changes
