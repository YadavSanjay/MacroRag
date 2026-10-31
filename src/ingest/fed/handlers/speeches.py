"""Endpoint 5 — Speeches & Testimony (Ingestion Design §4.5).

Same RSS schema as ``press_monetary`` plus a priority-filter layer: Chair/Vice
Chair speeches and monetary-policy-themed titles are tier-1 priority; other
voting-Governor speeches tier-2; ceremonial/community speeches tier-3 (low
retrieval weight). Speaker/role/venue are parsed from the title; long speeches
sub-chunk on paragraph clusters.
"""

from __future__ import annotations

from ...common import feeds, html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import FedHandler
from ..parsers import _as_date_or_none, date_from_url, parse_speech_title, speech_priority

log = get_logger(__name__)

PUBLICATION = "Fed Speech / Testimony"


class SpeechesHandler(FedHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        items = feeds.parse_rss(res.content, self.cfg.id, self.cfg.doc_type_rules)
        high_kw = self.cfg.content.get("high_priority_title_keywords", [])
        high_sp = self.cfg.content.get("high_priority_speakers", [])
        for item in items:
            sp = parse_speech_title(item.title)
            item.extra.update(sp)
            item.priority = speech_priority(item.title, sp.get("speaker_role"),
                                            high_kw, high_sp)
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        body = html_extract.extract_body(res.content,
                                         self.cfg.content.get("body_selectors"))
        fields = dict(item.extra)
        fields["priority_tier"] = item.priority

        doc = self._build_document(
            item=item,
            res=res,
            body_text=body,
            publication=PUBLICATION,
            release_date=date_from_url(item.url) or _as_date_or_none(item.published),
            chunking=item.chunking,  # "paragraphs"
            fields=fields,
        )
        return [doc] if doc else []
