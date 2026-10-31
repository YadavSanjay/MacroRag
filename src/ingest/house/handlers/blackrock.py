"""Endpoint 2 — BlackRock Investment Institute (Ingestion Design §8.2).

The publications archive's article LIST is JS-rendered, but BII's individual
landing pages (weekly commentary, investment perspective) are server-rendered
with the latest full article body — so discovery uses configured direct URLs
(the weekly commentary is BII's operative tactical view). Each article is tagged
with which of BII's five "mega forces" frame it (keyword scan; refine downstream
via the LLM tagging seam). Outlook PDFs reuse the shared PDF pipeline.
"""

from __future__ import annotations

from ...common import dateextract, html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import HouseHandler
from ..parsers import slug_from_url

log = get_logger(__name__)

PUBLICATION = "BlackRock Investment Institute"


class BlackRockHandler(HouseHandler):

    def discover(self) -> list[DiscoveredItem]:
        items: list[DiscoveredItem] = []
        for entry in self.cfg.content.get("direct_urls", []):
            url = entry["url"]
            items.append(DiscoveredItem(
                source_id=self.cfg.id,
                title=entry.get("publication", slug_from_url(url)),
                url=url, doc_type=entry.get("doc_type", "bii_article"),
                chunking="whole", priority=1, is_pdf=url.lower().endswith(".pdf"),
                extra={"slug": slug_from_url(url),
                       "publication": entry.get("publication", PUBLICATION)},
            ))
        log.info("[%s] %d BII direct article(s) to ingest", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        publication = item.extra.get("publication", PUBLICATION)
        if item.is_pdf:
            return self._ingest_pdf(
                item=item, publication=publication, release_date=None,
                derive_date_if_missing=True,
                extra_fields={"slug": item.extra.get("slug")})

        res = self._fetch_content(item.url)
        if res is None:
            return []
        body = html_extract.extract_article(res.content)
        fields = {
            "slug": item.extra.get("slug"),
            "mega_forces_referenced": self._tag_mega_forces(body),
        }
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=publication,
            release_date=dateextract.extract_date_from_html(
                res.content, max_date=self.ctx.today),
            chunking="whole", fields=fields)
        return [doc] if doc else []

    def _tag_mega_forces(self, text: str) -> list[str]:
        low = (text or "").lower()
        return [mf for mf in self.cfg.content.get("mega_forces", []) if mf in low]
