"""Endpoint 1 — JPM Eye on the Market / Cembalest (Ingestion Design §8.1).

The most complex Tier-5 source. The hub HTML links the
stable ``{slug}-amv.pdf`` artifacts; Stage B runs them through the shared PDF
pipeline. EOTM is the one Tier-5 source ingested as public-with-attribution (it
carries the explicit "not JPM Research" disclaimer), so the author attribution
(Cembalest) is preserved on every chunk. Each PDF is multi-topic; token-budget
sub-chunking approximates the per-topic split (topic-header detection is a
documented downstream refinement).
"""

from __future__ import annotations

from ...common import html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import HouseHandler
from ..parsers import slug_from_url

log = get_logger(__name__)

PUBLICATION = "JPM Eye on the Market"


class JpmEotmHandler(HouseHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        c = self.cfg.content
        links = html_extract.find_links(res.content, self.cfg.discovery.url,
                                        href_pattern=c.get("pdf_link_pattern", r"amv\.pdf"))
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for url, anchor in links:
            if url in seen:
                continue
            seen.add(url)
            slug = slug_from_url(url)
            items.append(DiscoveredItem(
                # slug_from_url may return None, so fall back to the URL rather
                # than handing DiscoveredItem an untitled entry
                source_id=self.cfg.id, title=anchor or slug or url, url=url,
                doc_type="jpm_eotm", chunking="sections", priority=1,
                is_pdf=True, extra={"slug": slug},
            ))
            if len(items) >= int(c.get("max_pdfs", 5)):
                break
        log.info("[%s] discovered %d EOTM PDF(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        # Multi-topic PDF -> token-budget sub-chunking via the "sections" strategy
        # (no fixed headings); keeps Cembalest attribution on every chunk.
        return self._ingest_pdf(
            item=item, publication=PUBLICATION, release_date=None,
            chunking="sections", derive_date_if_missing=True,
            extra_fields={"author": self.cfg.content.get("author"),
                          "slug": item.extra.get("slug")},
        )
