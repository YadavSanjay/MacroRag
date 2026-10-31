"""SIA monthly global semiconductor sales (Ingestion Design §9B.3).

Scrapes the market-data archive for press-release links, then extracts the
headline numbers (total sales, YoY) plus a short narrative chunk. Best-effort:
a blocked/empty fetch degrades to a no-op rather than failing the run.
"""

from __future__ import annotations

from ...common import dateextract, html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import ChipsHandler
from ..parsers import parse_sia_release

log = get_logger(__name__)

PUBLICATION = "SIA Global Semiconductor Sales"


class SiaHandler(ChipsHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_or_render(self.cfg.discovery.url)
        if res is None:
            return []
        links = html_extract.find_links(
            res.content, self.cfg.discovery.url,
            href_pattern=self.cfg.content.get("release_link_pattern",
                                              "global-semiconductor-sales"))
        items, seen = [], set()
        for url, anchor in links:
            if url in seen:
                continue
            seen.add(url)
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=anchor or "SIA monthly sales",
                url=url, doc_type="sia_monthly", chunking="whole", priority=1))
            if len(items) >= int(self.cfg.content.get("max_releases", 4)):
                break
        log.info("[%s] discovered %d SIA release(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        body = html_extract.extract_article(res.content)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            release_date=dateextract.extract_date_from_html(
                res.content, max_date=self.ctx.today),
            chunking="whole", fields=parse_sia_release(body))
        return [doc] if doc else []
