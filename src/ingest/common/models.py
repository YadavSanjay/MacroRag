"""Runtime data models shared across all ingestion tiers.

Three layers map onto the two-stage pattern (Ingestion Design §4.0):

* :class:`DiscoveredItem` — Stage A output: a candidate document found in a
  discovery feed / index, not yet fetched.
* :class:`FetchResult` — the raw HTTP response (bytes + cache headers) from a
  single content fetch, persisted verbatim before parsing.
* :class:`ParsedDocument` / :class:`Chunk` — Stage B output: the parsed, chunked,
  provenance-stamped document ready for embedding + indexing downstream.

``ParsedDocument`` also carries an optional ``timeseries`` payload for the
numeric/narrative split (e.g. SEP projection tables, DFAST scenario paths,
SLOOS net-tightening series) that routes to the time-series store rather than
the vector store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

# ── Stage A — discovery ──────────────────────────────────────────────────────


class DiscoveredItem(BaseModel):
    """A single candidate document surfaced by a discovery feed or index page."""

    source_id: str                       # endpoint id, e.g. "press_monetary"
    title: str
    url: str                             # content URL to fetch in Stage B
    doc_type: str = "unknown"            # classified via config doc_type_rules
    chunking: str = "whole"              # whole | sections | paragraphs | pdf_sections
    published: datetime | None = None
    priority: int = 1                    # 1 = highest; drives retrieval weight
    is_pdf: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


# ── Stage B fetch — raw bytes ────────────────────────────────────────────────


@dataclass
class FetchResult:
    """Raw HTTP fetch outcome. ``content`` holds bytes (excluded from any JSON)."""

    url: str
    status_code: int
    content: bytes = b""
    content_type: str = ""
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str = ""
    from_cache: bool = False             # True when a 304 short-circuited the body
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and bool(self.content)

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304


# ── Stage B parsed — chunks + provenance ─────────────────────────────────────


class ChunkMetadata(BaseModel):
    """Provenance + routing metadata stamped on every chunk (Ingestion Design §2.8).

    Mirrors the ARag macro-collection payload schema so chunks drop straight
    into Qdrant: grounding prefix is ``publisher | publication | release_date``.
    """

    publisher: str
    publication: str
    release_date: date | None = None
    retrieval_date: date
    url: str
    etag: str | None = None
    content_hash: str
    licence: str = "public_domain_us_gov"
    tier: int = 0
    bucket: str = "C"                    # A=catalyst, B=periodic, C=rolling (§3.2)
    doc_type: str = "unknown"
    section: str | None = None
    entity_tags: list[str] = Field(default_factory=list)
    factor_tags: list[str] = Field(default_factory=list)
    trades_affected: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)  # source-specific fields
    # Exact horizon (trading days) of the move this chunk describes, e.g. a
    # market-episode card's 5- or 10-day window; None for ordinary documents. Read
    # by synthesis.validate.check_source_horizon once it reaches the payload
    # (not yet carried through utils.corpus).
    horizon_days: int | None = None


class Chunk(BaseModel):
    """A retrieval-ready text chunk with its grounding prefix and metadata."""

    chunk_id: str
    seq: int
    text: str
    metadata: ChunkMetadata

    def grounded_text(self) -> str:
        """Cheap entity-grounding prefix prepended before embedding (§9A)."""
        m = self.metadata
        rel = m.release_date.isoformat() if m.release_date else "n.d."
        prefix = f"{m.publisher} | {m.publication} | {rel}"
        if m.section:
            prefix += f" | {m.section}"
        return f"{prefix}\n\n{self.text}"


class ParsedDocument(BaseModel):
    """A fully parsed source document: chunks for the vector store plus an
    optional numeric ``timeseries`` payload for the time-series store."""

    doc_id: str
    source_id: str
    title: str
    doc_type: str
    publisher: str
    publication: str
    release_date: date | None = None
    retrieval_date: date
    url: str
    content_hash: str
    licence: str = "public_domain_us_gov"
    tier: int = 0
    bucket: str = "C"
    raw_path: str | None = None       # path to persisted raw bytes
    fields: dict[str, Any] = Field(default_factory=dict)   # extracted scalars
    timeseries: list[dict[str, Any]] = Field(default_factory=list)  # -> ts store
    chunks: list[Chunk] = Field(default_factory=list)
