"""Endpoint 5 — Apollo Academy / The Daily Spark (Ingestion Design §8.5).

Simplest content, highest cadence, most directly
relevant (Slok's AI-capex-concentration thesis). apolloacademy.com is WordPress,
so the wp-json REST API returns slug + date + rendered body in one call: no
per-post second fetch needed. Each post is one chunk (200-600 words).
"""

from __future__ import annotations

import json
from datetime import datetime

from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import HouseHandler
from ..parsers import html_to_text

log = get_logger(__name__)

PUBLICATION = "Apollo Daily Spark"


class ApolloHandler(HouseHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        try:
            posts = json.loads(res.content)
        except json.JSONDecodeError as exc:
            log.error("[%s] wp-json parse failed: %s", self.cfg.id, exc)
            return []

        link_filter = self.cfg.content.get("link_filter", "")
        items: list[DiscoveredItem] = []
        for p in posts:
            link = p.get("link", "")
            if link_filter and link_filter not in link:
                continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id,
                title=html_to_text(p.get("title", {}).get("rendered", "")),
                url=link, doc_type="apollo_daily_spark", chunking="whole",
                priority=1, extra={"post": p},
            ))
            if len(items) >= int(self.cfg.content.get("max_posts", 12)):
                break
        log.info("[%s] discovered %d Daily Spark post(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        p = item.extra["post"]
        body_html = p.get("content", {}).get("rendered", "")
        body = html_to_text(body_html)
        fields = {
            "author": self.cfg.content.get("author"),
            "post_slug": p.get("slug"),
            "wp_post_id": p.get("id"),
        }
        return self._emit_text_doc(
            item=item, url=item.url,
            raw_bytes=body_html.encode("utf-8", "ignore"),
            body_text=body, publication=PUBLICATION,
            release_date=_parse_date(p.get("date")), fields=fields,
        )


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None
