"""Shared base for Tier 5 (house-view) handlers.

Adds the heavy-disclaimer body-cleaning hook (§8.6 #7) and an HTML-article
ingest helper (fetch → extract body → license-gated, attribution-stamped
document). PDF ingestion (JPM EOTM, BlackRock Outlooks) reuses the shared
:meth:`BaseSourceHandler._ingest_pdf`.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ..common import html_extract
from ..common.handler_base import BaseSourceHandler
from ..common.http_client import sha256_hex
from ..common.logging_utils import get_logger
from ..common.models import DiscoveredItem, FetchResult, ParsedDocument
from ..common.storage import filename_from_url, safe_name
from .parsers import strip_house_boilerplate

log = get_logger(__name__)


class HouseHandler(BaseSourceHandler):
    """Base for the five Tier-5 house-view handlers."""

    def clean_body(self, text: str) -> str:
        return strip_house_boilerplate(text)

    def _emit_text_doc(
        self,
        *,
        item: DiscoveredItem,
        url: str,
        raw_bytes: bytes,
        body_text: str,
        publication: str,
        release_date: date | None,
        fields: dict | None = None,
        chunking: str = "whole",
    ) -> list[ParsedDocument]:
        """Build a document from already-fetched text (e.g. a JSON body field).

        Avoids a redundant second HTTP fetch when discovery already returned the
        content (Apollo wp-json ``content.rendered``), while keeping raw-bytes
        persistence + idempotency + license-gating.
        """
        content_hash = sha256_hex(raw_bytes)
        if self.ctx.cache.is_processed(url, content_hash):
            log.debug("[%s] unchanged, skipping: %s", self.cfg.id, url)
            return []
        self.ctx.raw_store.save(self.tier.tier, self.cfg.id,
                                safe_name(filename_from_url(url)), raw_bytes)
        res = FetchResult(url=url, status_code=200, content_hash=content_hash)
        doc = self._build_document(
            item=item, res=res, body_text=body_text, publication=publication,
            release_date=release_date, chunking=chunking, fields=fields or {})
        return [doc] if doc else []

    def _ingest_html(
        self,
        *,
        item: DiscoveredItem,
        publication: str,
        release_date: date | None,
        fields: dict | None = None,
        body_text: str | None = None,
        chunking: str = "whole",
    ) -> list[ParsedDocument]:
        """Fetch an article page (unless ``body_text`` is supplied) and build a doc.

        ``body_text`` lets callers pass already-extracted text (e.g. a WordPress
        ``content.rendered`` field) while still going through the shared
        license-gating + attribution + chunking path.
        """
        res = self._fetch_content(item.url)
        if res is None:
            return []
        if body_text is None:
            body_text = html_extract.extract_body(res.content)
        doc = self._build_document(
            item=item, res=res, body_text=body_text, publication=publication,
            release_date=release_date, chunking=chunking, fields=fields,
        )
        return [doc] if doc else []
