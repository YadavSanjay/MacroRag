"""Endpoint 2 — Financial Stability Report (Ingestion Design §4.2).

No RSS feed: Stage A scrapes the publications landing page for the dated FSR
PDFs. Bucket B (latest supersedes), so by default only the most recent couple of
editions are ingested. Stage B runs the PDF through text+table extraction,
sub-chunks on the stable FSR section structure, and routes extracted tables to
the time-series payload (the "Chart Data and Descriptions" numeric backbone).
"""

from __future__ import annotations

from ...common import html_extract, pdf_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import FedHandler
from ..parsers import date_from_url, tables_to_timeseries

log = get_logger(__name__)

PUBLICATION = "Financial Stability Report"
DEFAULT_MAX_EDITIONS = 2


class FinancialStabilityHandler(FedHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        pattern = self.cfg.content.get("pdf_link_pattern",
                                       r"financial-stability-report-\d{6,8}\.pdf")
        links = html_extract.find_links(res.content, self.cfg.discovery.url,
                                        href_pattern=pattern)
        # Sort newest-first by the date embedded in the PDF slug, keep top-N.
        dated = sorted(
            links,
            key=lambda lk: (date_from_url(lk[0]) or _MIN_DATE()),
            reverse=True,
        )
        max_editions = int(self.cfg.content.get("max_editions", DEFAULT_MAX_EDITIONS))
        items: list[DiscoveredItem] = []
        for url, anchor in dated[:max_editions]:
            rd = date_from_url(url)
            items.append(DiscoveredItem(
                source_id=self.cfg.id,
                title=f"{PUBLICATION} {rd.isoformat() if rd else ''}".strip(),
                url=url,
                doc_type="financial_stability_report",
                chunking="pdf_sections",
                priority=1,
                is_pdf=True,
            ))
        log.info("[%s] discovered %d FSR edition(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []

        pages = pdf_extract.extract_pages(res.content, with_tables=True)
        body = "\n\n".join(p.text for p in pages if p.text)
        timeseries: list[dict] = []
        for p in pages:
            if p.tables:
                timeseries.extend(tables_to_timeseries(p.tables, source="FSR",
                                                       page=p.number))

        doc = self._build_document(
            item=item,
            res=res,
            body_text=body,
            publication=PUBLICATION,
            release_date=date_from_url(item.url),
            chunking="pdf_sections",
            section_headings=self.cfg.content.get("section_headings"),
            fields={"page_count": len(pages)},
            timeseries=timeseries,
        )
        return [doc] if doc else []


def _MIN_DATE():
    from datetime import date
    return date.min
