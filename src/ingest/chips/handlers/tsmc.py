"""TSMC monthly sales + quarterly capex (Ingestion Design §9B.4).

Highest-frequency foundry-demand proxy (leads EDGAR capex by weeks). tsmc.com
403s a normal request, so this uses the HTTP-then-headless fallback. Best-effort:
if both are blocked, degrades to a no-op (SIA + the Federal Register feed remain
the operative Taiwan-tail signals).
"""

from __future__ import annotations

from ...common import dateextract, html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import ChipsHandler
from ..parsers import parse_tsmc_release

log = get_logger(__name__)

PUBLICATION = "TSMC Press Release"


class TsmcHandler(ChipsHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_or_render(self.cfg.discovery.url)
        if res is None:
            log.warning("[%s] TSMC press-release index unavailable (bot-walled); "
                        "skipping.", self.cfg.id)
            return []
        links = html_extract.find_links(
            res.content, self.cfg.discovery.url,
            href_pattern=self.cfg.content.get("release_link_pattern"))
        items, seen = [], set()
        for url, anchor in links:
            if url in seen:
                continue
            seen.add(url)
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=anchor or "TSMC press release",
                url=url, doc_type="tsmc_release", chunking="whole", priority=1))
            if len(items) >= int(self.cfg.content.get("max_releases", 6)):
                break
        log.info("[%s] discovered %d TSMC release(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_or_render(item.url)
        if res is None:
            return []
        # pr.tsmc.com defeats trafilatura (nav + cookie banner come through), so a
        # configured selector takes precedence.
        selectors = self.cfg.content.get("body_selectors")
        body = (html_extract.extract_body(res.content, selectors) if selectors
                else html_extract.extract_article(res.content))
        # pr.tsmc.com carries no date metadata and its nav pushes the dateline past
        # the page-head scan; the article body opens with "Issued on: ...".
        release_date = (
            dateextract.extract_date_from_html(res.content, max_date=self.ctx.today)
            or dateextract.extract_date_from_text(body, max_date=self.ctx.today))
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            release_date=release_date,
            chunking="whole", fields=parse_tsmc_release(body))
        return [doc] if doc else []
