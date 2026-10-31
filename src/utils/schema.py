"""Typed data contracts shared across embeddings / vectorstore / retriever / llm.

Kept as light dataclasses (not pydantic) so the hot retrieval path has no
validation overhead; the LLM structured-IO models live in the ``llm`` package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ── embedding output ─────────────────────────────────────────────────────────


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]


@dataclass
class Embedding:
    dense: list[float]
    sparse: SparseVector | None = None


# ── corpus / index points ────────────────────────────────────────────────────


@dataclass
class ChunkPoint:
    """A vector-store point built from one ingest chunk."""

    point_id: str                     # deterministic UUID (see text.point_uuid)
    chunk_id: str
    doc_id: str
    seq: int
    text: str                         # raw chunk body (returned to the LLM)
    embed_text: str                   # grounded text (what we embed)
    payload: dict[str, Any]           # filterable metadata + provenance


# ── query understanding ──────────────────────────────────────────────────────


@dataclass
class MetaFilter:
    """MacroRAG facet filter (the analog of ARag's year filter, richer)."""

    tiers: list[int] = field(default_factory=list)
    buckets: list[str] = field(default_factory=list)
    doc_types: list[str] = field(default_factory=list)
    entity_tags: list[str] = field(default_factory=list)
    factor_tags: list[str] = field(default_factory=list)
    publishers: list[str] = field(default_factory=list)
    date_from: str | None = None   # ISO date inclusive
    date_to: str | None = None     # ISO date inclusive
    # Chunks carrying any of these factor tags skip the entity facet (all other
    # facets still apply). Synthesize sets it so heuristic entity tags cannot shut
    # out the quantified scenario studies; extract_filters drops it when the caller
    # supplied entity_tags, which stay a hard filter.
    entity_exempt_factor_tags: list[str] = field(default_factory=list)
    # Dormant data-isolation hook. Nothing writes it at ingest, so it partitions
    # nothing today; the API stamps the caller's *principal* here so retrieval
    # would discriminate correctly if per-tenant ingest were ever added. A
    # principal is an auth/rate-limit/cost identity, NOT a boundary (Production Hardening §A).
    tenant_id: str | None = None

    def is_empty(self) -> bool:
        return not any([self.tiers, self.buckets, self.doc_types, self.entity_tags,
                        self.factor_tags, self.publishers, self.date_from,
                        self.date_to, self.tenant_id])


@dataclass
class QueryAnalysis:
    original: str
    route: str = "DIRECT"             # DIRECT | PARAPHRASE | DECOMPOSE | HYDE
    paraphrase: str | None = None
    subqueries: list[str] = field(default_factory=list)
    hyde_docs: list[str] = field(default_factory=list)
    filters: MetaFilter = field(default_factory=MetaFilter)

    def search_queries(self) -> list[str]:
        """All text variants to run retrieval branches on."""
        qs = [self.paraphrase or self.original]
        qs.extend(self.subqueries)
        qs.extend(self.hyde_docs)
        # de-dupe, preserve order
        seen, out = set(), []
        for q in qs:
            q = (q or "").strip()
            if q and q not in seen:
                seen.add(q)
                out.append(q)
        return out


# ── retrieval results ────────────────────────────────────────────────────────


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float
    payload: dict[str, Any]
    rerank_score: float | None = None

    @property
    def doc_id(self) -> str:
        return self.payload.get("doc_id", "")


@dataclass
class TimeseriesHit:
    source: str                       # e.g. DFAST_2026, SEP_2026-06
    publisher: str
    rows: list[dict[str, Any]]
    note: str = ""


# ── generation output ────────────────────────────────────────────────────────


@dataclass
class AnswerDetail:
    text: str
    chunk_ids: list[str] = field(default_factory=list)


@dataclass
class Answer:
    summary: str
    details: list[AnswerDetail] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    used_chunk_ids: list[str] = field(default_factory=list)
    timeseries: list[TimeseriesHit] = field(default_factory=list)
    refused: bool = False              # relevance floor not met -> no generation
    unsupported: list[str] = field(default_factory=list)  # claims dropped by verifier
