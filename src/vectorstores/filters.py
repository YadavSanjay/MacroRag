"""MacroRAG facet filtering — pure-python predicate (no qdrant dependency).

Used by the in-memory store and unit tests; the Qdrant adapter builds the
equivalent server-side ``Filter`` from the same :class:`MetaFilter`.
"""

from __future__ import annotations

from typing import Any

from utils.schema import MetaFilter


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def match_payload(mf: MetaFilter, payload: dict[str, Any]) -> bool:
    """True if a chunk payload satisfies every set facet of ``mf`` (AND across
    facets; OR within a facet)."""
    if mf.tiers and payload.get("tier") not in mf.tiers:
        return False
    if mf.buckets and payload.get("bucket") not in mf.buckets:
        return False
    if mf.doc_types and payload.get("doc_type") not in mf.doc_types:
        return False
    if mf.publishers and payload.get("publisher") not in mf.publishers:
        return False
    if mf.entity_tags:
        tags = set(_as_list(payload.get("entity_tags")))
        exempt = set(_as_list(payload.get("factor_tags"))) & set(
            mf.entity_exempt_factor_tags)
        if tags.isdisjoint(mf.entity_tags) and not exempt:
            return False
    if mf.factor_tags:
        tags = set(_as_list(payload.get("factor_tags")))
        if tags.isdisjoint(mf.factor_tags):
            return False
    rel = payload.get("release_date")
    if mf.date_from and (rel is None or str(rel) < mf.date_from):
        return False
    if mf.date_to and (rel is None or str(rel) > mf.date_to):
        return False
    # Dormant tenant hook: no ingestion path writes tenant_id today, so every
    # payload is the shared public corpus and this branch never excludes anything.
    # It is retained so retrieval already discriminates correctly IF a private
    # per-tenant ingest path is ever added (a tenant would then see its own docs
    # plus the shared corpus). What the API stamps here is the caller's
    # **principal** — an auth/rate-limit/cost identity, NOT a data-isolation
    # boundary — see docs/design/Macro_RAG_Production_Hardening.md §A.
    if mf.tenant_id and payload.get("tenant_id") not in (None, mf.tenant_id):
        return False
    return True
