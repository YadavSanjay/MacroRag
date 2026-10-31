"""Load the ingest output (processed ``ParsedDocument`` JSON) into the shapes the
RAG layer consumes: :class:`ChunkPoint` records for the vector store and flat
time-series rows for the numeric store.

Decoupled from the ingest package on purpose — it reads the persisted JSON
contract, not the ingest models, so the two layers evolve independently.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Union

from .logging import get_logger
from .objectstore import LocalObjectStore, ObjectStore
from .schema import ChunkPoint
from .text import grounded_text, point_uuid

log = get_logger(__name__)

# A corpus source is either an object store or a local directory path (back-compat:
# tests + callers still pass ``settings.processed_path`` directly).
CorpusSource = Union[ObjectStore, Path, str]


def _as_store(source: CorpusSource) -> ObjectStore:
    if isinstance(source, ObjectStore):
        return source
    return LocalObjectStore(Path(source))

# Chunk-metadata fields promoted to the Qdrant payload (filterable facets +
# provenance). Everything here can be used as a retrieval filter or a citation.
_FACET_FIELDS = (
    "publisher", "publication", "release_date", "retrieval_date", "url",
    "content_hash", "licence", "tier", "bucket", "doc_type", "section",
    "entity_tags", "factor_tags", "trades_affected",
    # Exact horizon (trading days) of the move a chunk describes (market-episode
    # cards); read by synthesis.validate.check_source_horizon. Omitted when None.
    "horizon_days",
)


def iter_documents(source: CorpusSource) -> Iterator[dict[str, Any]]:
    store = _as_store(source)
    for key in store.iter_keys(suffix=".json"):
        text = store.get_text(key)
        if text is None:
            continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover
            log.warning("skipping unreadable doc %s: %s", key, exc)


def _chunk_payload(doc: dict, chunk: dict, prev_id: str | None,
                   next_id: str | None) -> dict[str, Any]:
    meta = chunk.get("metadata", {})
    payload: dict[str, Any] = {k: meta[k] for k in _FACET_FIELDS if meta.get(k) not in (None, [], "")}
    payload.update({
        "doc_id": doc["doc_id"],
        "source_id": doc.get("source_id", ""),
        "title": doc.get("title", ""),
        "chunk_id": chunk["chunk_id"],
        "seq": chunk.get("seq", 0),
        "prev_chunk_id": prev_id,
        "next_chunk_id": next_id,
    })
    # release_date as an ISO string is already range-filterable lexicographically.
    return payload


def iter_chunk_points(source: CorpusSource, *, use_grounding: bool = True
                      ) -> Iterator[ChunkPoint]:
    """Yield a :class:`ChunkPoint` per ingest chunk, with prev/next neighbours."""
    for doc in iter_documents(source):
        chunks = doc.get("chunks") or []
        ids = [c["chunk_id"] for c in chunks]
        for i, chunk in enumerate(chunks):
            prev_id = ids[i - 1] if i > 0 else None
            next_id = ids[i + 1] if i + 1 < len(ids) else None
            payload = _chunk_payload(doc, chunk, prev_id, next_id)
            text = chunk.get("text", "")
            embed_text = grounded_text(payload, text) if use_grounding else text
            yield ChunkPoint(
                point_id=point_uuid(chunk["chunk_id"]),
                chunk_id=chunk["chunk_id"],
                doc_id=doc["doc_id"],
                seq=chunk.get("seq", 0),
                text=text,
                embed_text=embed_text,
                payload=payload,
            )


def iter_timeseries_rows(source: CorpusSource) -> Iterator[dict[str, Any]]:
    """Yield flattened numeric rows (one per observation) with doc provenance.

    Covers the chunk-free numeric docs (DFAST/SEP/BEA/EIA/GPR…) plus any narrative
    doc that also carries a ``timeseries`` payload.
    """
    for doc in iter_documents(source):
        ts = doc.get("timeseries") or []
        if not ts:
            continue
        base = {
            "doc_id": doc["doc_id"],
            "source_id": doc.get("source_id", ""),
            "tier": doc.get("tier"),
            "publisher": doc.get("publisher", ""),
            "publication": doc.get("publication", ""),
            "url": doc.get("url", ""),
            "release_date": doc.get("release_date"),
        }
        for row in ts:
            merged = dict(base)
            merged.update(row)
            yield merged
