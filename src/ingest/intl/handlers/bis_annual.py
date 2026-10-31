"""Endpoint 4 — BIS Annual Economic Report (Ingestion Design §7.4).

Single ~150pp PDF per year at ``aer-{YYYY}.pdf`` (``ar{YYYY}e.pdf`` before the
2026 bis.org move). Discovery is URL construction over recent years (newest
first), taking the latest that exists. The financial-system chapter is the
highest-signal section for this book.
"""

from __future__ import annotations

from datetime import date

from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import IntlHandler
from ..parsers import recent_years

log = get_logger(__name__)

PUBLICATION = "BIS Annual Economic Report"


class BisAnnualHandler(IntlHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        items: list[DiscoveredItem] = []
        for year in recent_years(self.ctx.today, int(c.get("years_back", 1))):
            url = c["pdf_url_pattern"].replace("{YYYY}", str(year))
            if not self._exists(url):
                continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=f"{PUBLICATION} {year}", url=url,
                doc_type="bis_annual_report", chunking="pdf_sections",
                priority=1, is_pdf=True,
                extra={"release_date": date(year, 6, 1), "report_year": year},
            ))
            if len(items) >= int(c.get("max_issues", 1)):
                break
        log.info("[%s] discovered %d BIS AER edition(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        return self._ingest_pdf(
            item=item, publication=PUBLICATION,
            release_date=item.extra.get("release_date"),
            section_headings=self.cfg.content.get("section_headings"),
            extra_fields={"report_year": item.extra.get("report_year")},
        )
