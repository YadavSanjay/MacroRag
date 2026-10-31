"""Small text helpers shared across the RAG layer: grounding prefix, stable point
ids, and a cheap token estimate."""

from __future__ import annotations

import uuid
from typing import Any

# Stable namespace so the same chunk_id always maps to the same Qdrant point id.
_NS = uuid.UUID("6f6d6163-726f-7261-6700-6d6163726f72")  # arbitrary fixed UUID


def point_uuid(chunk_id: str) -> str:
    """Deterministic UUID5 for a chunk_id (Qdrant requires int/UUID point ids)."""
    return str(uuid.uuid5(_NS, chunk_id))


def grounding_prefix(payload: dict[str, Any]) -> str:
    """``publisher | publication | release_date [| section]`` grounding header.

    Mirrors ``ingest`` ``Chunk.grounded_text`` — prepended before embedding so the
    encoder sees entity/date-aware context, not just generic macro prose.
    """
    rel = payload.get("release_date") or "n.d."
    parts = [str(payload.get("publisher", "")).strip(),
             str(payload.get("publication", "")).strip(),
             str(rel)]
    prefix = " | ".join(p for p in parts if p)
    section = payload.get("section")
    if section:
        prefix += f" | {section}"
    return prefix


def grounded_text(payload: dict[str, Any], text: str) -> str:
    return f"{grounding_prefix(payload)}\n\n{text}"


def estimate_tokens(text: str, chars_per_token: int = 4) -> int:
    return max(1, len(text) // max(chars_per_token, 1))
