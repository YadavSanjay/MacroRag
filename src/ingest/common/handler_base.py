"""Generic source-handler base shared by every tier.

Centralises the tier-agnostic mechanics every endpoint handler repeats:
conditional-GET discovery fetch, content fetch with raw-bytes persistence +
idempotency, provenance-stamped :class:`ChunkMetadata` construction, and
:class:`ParsedDocument` assembly with chunking.

Tier packages subclass this (``FedHandler``, ``TreasuryHandler``) and add only
their site-specific discovery + parsing. A ``clean_body`` hook lets a tier strip
site-specific boilerplate (e.g. the Treasury FREEDOM250 banner) without
overriding the assembly path.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import httpx

from . import pdf_extract, structured
from .base import EndpointHandler, IngestContext
from .config import EndpointConfig
from .http_client import sha256_hex
from .logging_utils import get_logger
from .models import (
    Chunk,
    ChunkMetadata,
    DiscoveredItem,
    FetchResult,
    ParsedDocument,
)
from .storage import filename_from_url, safe_name

log = get_logger(__name__)


class BaseSourceHandler(EndpointHandler):
    """Concrete helper base for tier endpoint handlers."""

    def __init__(self, cfg: EndpointConfig, ctx: IngestContext):
        super().__init__(cfg, ctx)
        self.tier = ctx.tier
        # Per-endpoint overrides (Tier 3 mixes BEA/BLS/CBO; Tier 5 mixes licences).
        self.publisher = cfg.publisher or ctx.tier.publisher
        self.licence = cfg.licence or ctx.tier.licence

    # ── shared discovery / content fetch ─────────────────────────────────────

    def _fetch_discovery(self) -> FetchResult | None:
        """Stage A fetch with conditional GET; ``None`` means nothing changed.

        Under ``ctx.refresh`` the cache is bypassed so the feed/index is re-listed
        in full (used by "fetch latest available" catch-up runs).
        """
        res = self.ctx.client.get(self.cfg.discovery.url,
                                  use_cache=not self.ctx.refresh)
        if res.not_modified:
            log.info("[%s] discovery unchanged (304)", self.cfg.id)
            return None
        return res

    def _fetch_content(self, url: str, *, name: str | None = None) -> FetchResult | None:
        """Stage B fetch: download, persist raw bytes, skip if already parsed.

        A 4xx (bot-wall / moved / gated) is treated as a graceful skip rather
        than an error — content-access failures shouldn't fail the run.
        """
        try:
            res = self.ctx.client.get(url)
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500:
                log.warning("[%s] content fetch %s -> HTTP %d; skipping.",
                            self.cfg.id, url, exc.response.status_code)
                return None
            raise
        if res.not_modified or not res.ok:
            return None
        if self.ctx.cache.is_processed(url, res.content_hash):
            log.debug("[%s] already processed, skipping: %s", self.cfg.id, url)
            return None
        self.ctx.raw_store.save(
            self.tier.tier, self.cfg.id, name or filename_from_url(url), res.content
        )
        return res

    def _fetch_or_render(self, url: str) -> FetchResult | None:
        """GET ``url``; on a 403/429 bot-wall, fall back to the headless browser.

        Shared by sources that intermittently bot-wall a plain HTTP request
        (tsmc.com, ferc.gov, semiconductors.org). Returns ``None`` (graceful
        degrade) if both paths fail.
        """
        try:
            res = self.ctx.client.get(url, use_cache=not self.ctx.refresh)
            if not (res.not_modified or not res.ok):
                return res
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (403, 429):
                raise
            log.info("[%s] %s returned %d; trying headless browser.",
                     self.cfg.id, url, exc.response.status_code)
        return self._fetch_rendered(url)

    # ── headless-browser fetch (JS-rendered sources, §8.6 #4) ────────────────

    def _render(self, url: str, *, wait_selector: str | None = None,
                settle_ms: int = 2500) -> bytes | None:
        """Render ``url`` with the shared headless browser; ``None`` if disabled."""
        from . import browser

        html = browser.render(url, user_agent=self.ctx.settings.http.user_agent,
                              wait_selector=wait_selector, settle_ms=settle_ms)
        return html.encode("utf-8", "ignore") if html else None

    def _fetch_rendered(self, url: str, *, wait_selector: str | None = None,
                        settle_ms: int = 2500,
                        name: str | None = None) -> FetchResult | None:
        """Browser-rendered analogue of :meth:`_fetch_content` (raw + idempotency)."""
        content = self._render(url, wait_selector=wait_selector, settle_ms=settle_ms)
        if not content:
            return None
        content_hash = sha256_hex(content)
        if self.ctx.cache.is_processed(url, content_hash):
            log.debug("[%s] already processed (rendered): %s", self.cfg.id, url)
            return None
        self.ctx.raw_store.save(self.tier.tier, self.cfg.id,
                                name or filename_from_url(url), content)
        return FetchResult(url=url, status_code=200, content=content,
                           content_type="text/html", content_hash=content_hash)

    def _emit_numeric_doc(
        self,
        *,
        item: DiscoveredItem,
        url: str,
        raw_content: bytes,
        content_hash: str,
        publication: str,
        timeseries: list[dict],
        fields: dict,
        raw_name: str | None = None,
    ) -> ParsedDocument | None:
        """Assemble a chunk-free time-series document from a fetched payload.

        Shared by the pure-numeric sources (BEA/BLS, SDMX, GPR/EPU, BTOS): persists
        raw bytes, enforces idempotency on a (possibly redacted/synthetic) URL, and
        emits a document carrying only the ``timeseries`` payload.
        """
        if not timeseries:
            log.warning("[%s] no observations for %s", self.cfg.id, raw_name or url)
            return None
        if self.ctx.cache.is_processed(url, content_hash):
            log.debug("[%s] unchanged, skipping: %s", self.cfg.id, url)
            return None
        self.ctx.raw_store.save(self.tier.tier, self.cfg.id,
                                safe_name(raw_name or filename_from_url(url)),
                                raw_content)
        res = FetchResult(url=url, status_code=200, content_hash=content_hash)
        return self._build_document(
            item=item, res=res, body_text="", publication=publication,
            release_date=None, chunking="whole", fields=fields,
            timeseries=timeseries)

    # ── body cleaning hook (override per tier) ───────────────────────────────

    def clean_body(self, text: str) -> str:
        """Strip site-specific boilerplate. Default: no-op."""
        return text

    # ── metadata / document assembly ─────────────────────────────────────────

    def _base_metadata(
        self,
        *,
        url: str,
        release_date: date | None,
        content_hash: str,
        etag: str | None,
        doc_type: str,
        publication: str,
        extra: dict | None = None,
    ) -> ChunkMetadata:
        return ChunkMetadata(
            publisher=self.publisher,
            publication=publication,
            release_date=release_date,
            retrieval_date=self.ctx.today,
            url=url,
            etag=etag,
            content_hash=content_hash,
            licence=self.licence,
            tier=self.tier.tier,
            bucket=self.cfg.bucket,
            doc_type=doc_type,
            entity_tags=list(self.cfg.entity_tags),
            factor_tags=list(self.cfg.factor_tags),
            trades_affected=list(self.cfg.trades),
            extra=extra or {},
        )

    @staticmethod
    def _doc_id(source_id: str, release_date: date | None, url: str) -> str:
        stamp = release_date.isoformat() if release_date else "nodate"
        return f"{source_id}_{stamp}_{filename_from_url(url)}"

    def _ingest_pdf(
        self,
        *,
        item: DiscoveredItem,
        publication: str,
        release_date: date | None,
        chunking: str = "pdf_sections",
        section_headings: list[str] | None = None,
        with_tables: bool = False,
        ts_source: str | None = None,
        extra_fields: dict | None = None,
        derive_date_if_missing: bool = False,
    ) -> list[ParsedDocument]:
        """Fetch + parse a single PDF into a section-chunked document.

        Shared by every PDF-bearing tier (Fed FSR/DFAST, Treasury TBAC, IMF/BIS/
        BoJ reports, house Outlooks). Optionally routes extracted tables to the
        time-series payload. When ``derive_date_if_missing`` and ``release_date``
        is ``None``, best-effort a date from the PDF dateline / creation date so
        the chunks can still pass the synthesis citation gate.
        """
        res = self._fetch_content(item.url)
        if res is None:
            return []
        pages = pdf_extract.extract_pages(res.content, with_tables=with_tables)
        body = "\n\n".join(p.text for p in pages if p.text)
        if release_date is None and derive_date_if_missing:
            from . import dateextract
            release_date = (
                dateextract.extract_date_from_text(body, max_date=self.ctx.today)
                or dateextract.extract_date_from_pdf(res.content, max_date=self.ctx.today))
            if release_date:
                log.info("[%s] derived release_date %s for %s",
                         self.cfg.id, release_date, res.url)
        timeseries: list[dict] = []
        if with_tables and ts_source:
            for p in pages:
                if p.tables:
                    timeseries.extend(
                        structured.tables_to_rows(p.tables, source=ts_source,
                                                  page=p.number))
        fields = {"page_count": len(pages), **(extra_fields or {})}
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=publication,
            release_date=release_date, chunking=chunking,
            section_headings=section_headings, fields=fields,
            timeseries=timeseries,
        )
        return [doc] if doc else []

    def _build_document(
        self,
        *,
        item: DiscoveredItem,
        res: FetchResult,
        body_text: str,
        publication: str,
        release_date: date | None,
        chunking: str,
        section_headings: list[str] | None = None,
        fields: dict | None = None,
        timeseries: list[dict] | None = None,
        raw_path: str | None = None,
    ) -> ParsedDocument | None:
        """Clean + chunk ``body_text`` and assemble a persisted-ready document."""
        fields = fields or {}
        body_text = pdf_extract.sanitize_text(self.clean_body(body_text or ""))
        doc_id = self._doc_id(item.source_id, release_date, res.url)
        meta = self._base_metadata(
            url=res.url,
            release_date=release_date,
            content_hash=res.content_hash,
            etag=res.etag,
            doc_type=item.doc_type,
            publication=publication,
            extra=fields,
        )
        chunks: list[Chunk] = self.ctx.chunker.chunk(
            body_text,
            doc_id=doc_id,
            base_metadata=meta,
            strategy=chunking,
            section_headings=section_headings,
        )
        if not chunks and not timeseries:
            log.warning("[%s] no chunks/timeseries produced for %s",
                        self.cfg.id, res.url)
            return None
        return ParsedDocument(
            doc_id=doc_id,
            source_id=item.source_id,
            title=item.title,
            doc_type=item.doc_type,
            publisher=self.publisher,
            publication=publication,
            release_date=release_date,
            retrieval_date=self.ctx.today,
            url=res.url,
            content_hash=res.content_hash,
            licence=self.licence,
            tier=self.tier.tier,
            bucket=self.cfg.bucket,
            raw_path=raw_path,
            fields=fields,
            timeseries=timeseries or [],
            chunks=chunks,
        )
