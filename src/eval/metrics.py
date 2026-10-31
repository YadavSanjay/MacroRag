"""Pure retrieval metrics. A "gold" item declares which key the relevant chunks
should carry (``source_id``, ``doc_id``, ``doc_type``, …); we project each
retrieved chunk to that key and score the ranking. Provider-free and testable.

A projected key may be a scalar (``source_id``, ``doc_type``) OR a collection
(``factor_tags``, ``entity_tags`` are list-valued payload fields). ``_as_keys``
normalises either shape to the set of keys a chunk carries, so a chunk "hits"
when its key-set intersects the gold set. This keeps scalar behaviour identical
to a plain membership test while supporting multi-valued match fields."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from utils.schema import RetrievedChunk


def projected_keys(chunks: list[RetrievedChunk], field: str) -> list[Any]:
    """Project each retrieved chunk onto a payload field (the match key). The
    value is scalar or list depending on the field; the metrics below accept
    either via ``_as_keys``."""
    return [c.payload.get(field) for c in chunks]


def _as_keys(value: Any) -> set:
    """The set of match keys a single projected chunk carries. Scalars become a
    1-element set; lists/tuples/sets contribute every element; ``None`` (missing
    field) becomes the empty set so it never spuriously matches gold."""
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set, frozenset)):
        return set(value)
    return {value}


def hit_at_k(retrieved: list[Any], gold: Iterable[Any], k: int) -> float:
    gold = set(gold)
    return 1.0 if any(_as_keys(r) & gold for r in retrieved[:k]) else 0.0


def recall_at_k(retrieved: list[Any], gold: Iterable[Any], k: int) -> float:
    gold = set(gold)
    if not gold:
        return 0.0
    found: set = set()
    for r in retrieved[:k]:
        found |= _as_keys(r) & gold
    return len(found) / len(gold)


def reciprocal_rank(retrieved: list[Any], gold: Iterable[Any]) -> float:
    gold = set(gold)
    for i, r in enumerate(retrieved, start=1):
        if _as_keys(r) & gold:
            return 1.0 / i
    return 0.0


def aggregate(per_query: list[dict[str, float]]) -> dict[str, float]:
    """Mean each metric across queries."""
    if not per_query:
        return {}
    keys = per_query[0].keys()
    return {k: sum(q[k] for q in per_query) / len(per_query) for k in keys}
