"""Endpoint 3 — Goldman Sachs Top of Mind (Ingestion Design §8.3).

goldmansachs.com is a Next.js site that 403s a bot UA and renders both the issue
list AND the article bodies client-side — invisible to polite HTTP. So this is
the first consumer of the shared headless-browser fetch (§8.6 #4): Stage A
renders the index to harvest issue slugs; Stage B renders each issue page and
extracts the framing essay + Q&A body. One issue = one deep dive on a topic.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from ...common import browser, dateextract, html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import HouseHandler
from ..parsers import slug_from_url

log = get_logger(__name__)

PUBLICATION = "GS Top of Mind"
HOST = "https://www.goldmansachs.com"


class GsTomHandler(HouseHandler):

    def discover(self) -> list[DiscoveredItem]:
        # The issue list lazy-loads ~4s after domcontentloaded (networkidle never
        # settles — GS polls continuously), and GS anti-automation occasionally
        # serves a reduced page on a fresh render, so retry until it appears.
        pattern = self.cfg.content.get("article_url_pattern",
                                       r"/insights/top-of-mind/[a-z0-9-]+")
        paths: list[str] = []
        for attempt in range(3):
            rendered = self._render(self.cfg.discovery.url, settle_ms=5000)
            if rendered is None:
                if not browser.available():
                    log.warning("[%s] headless browser unavailable; GS Top of "
                                "Mind list is JS-rendered. Skipping (run "
                                "`playwright install chromium`).", self.cfg.id)
                return []
            paths = _unique(re.findall(pattern, rendered.decode("utf-8", "ignore")))
            if paths:
                break
            log.debug("[%s] reduced render (attempt %d); retrying", self.cfg.id, attempt + 1)
        items: list[DiscoveredItem] = []
        for path in paths:
            slug = slug_from_url(path)
            if not slug or slug == "top-of-mind":
                continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=slug.replace("-", " ").title(),
                url=urljoin(HOST, path), doc_type="gs_top_of_mind",
                chunking="whole", priority=1, extra={"slug": slug},
            ))
            if len(items) >= int(self.cfg.content.get("max_issues", 6)):
                break
        log.info("[%s] discovered %d Top of Mind issue(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_rendered(item.url, settle_ms=4500)
        if res is None:
            return []
        body = html_extract.extract_article(res.content)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            release_date=dateextract.extract_date_from_html(
                res.content, max_date=self.ctx.today),
            chunking="whole",
            fields={"issue_slug": item.extra.get("slug")},
        )
        return [doc] if doc else []


def _unique(seq: list[str]) -> list[str]:
    out: list[str] = []
    for s in seq:
        if s not in out:
            out.append(s)
    return out
