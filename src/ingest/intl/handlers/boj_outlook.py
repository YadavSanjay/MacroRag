"""Endpoint 5 — BoJ Outlook Report (Ingestion Design §7.5).

Quarterly (Jan/Apr/Jul/Oct outlook MPMs) projection report at
``gor{YYMM}a.pdf`` (English edition, ``/en/`` path). Drives the USD/JPY channel
(#27). Projection tables route to the time-series payload; the rest is chunked
on the stable I–IV section structure.
"""

from __future__ import annotations

from datetime import date

from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import IntlHandler
from ..parsers import recent_issue_yymm

log = get_logger(__name__)

PUBLICATION = "BoJ Outlook Report"


class BojOutlookHandler(IntlHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        items: list[DiscoveredItem] = []
        for cand in recent_issue_yymm(self.ctx.today,
                                      c.get("outlook_months", [1, 4, 7, 10]),
                                      int(c.get("years_back", 1))):
            url = c["pdf_url_pattern"].replace("{YYMM}", cand["yymm"])
            if not self._exists(url):
                continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=f"{PUBLICATION} — {cand['label']}",
                url=url, doc_type="boj_outlook_report", chunking="pdf_sections",
                priority=1, is_pdf=True,
                extra={"release_date": date(cand["year"], cand["month"], 1)},
            ))
            if len(items) >= int(c.get("max_issues", 1)):
                break
        log.info("[%s] discovered %d BoJ Outlook report(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        return self._ingest_pdf(
            item=item, publication=PUBLICATION,
            release_date=item.extra.get("release_date"),
            section_headings=self.cfg.content.get("section_headings"),
            with_tables=True, ts_source=self.cfg.content.get("ts_source"),
        )
