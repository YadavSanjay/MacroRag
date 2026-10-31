"""IEA Energy and AI report (Ingestion Design §9A.3).

Scrapes the report page for linked blob PDFs and runs them through the shared
PDF pipeline. IEA PDFs may be account-gated; if none are reachable the handler
degrades to a no-op (EIA AEO/STEO remain the operative power-forecast spine).
"""

from __future__ import annotations

from ...common import html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import PowerHandler

log = get_logger(__name__)

PUBLICATION = "IEA Energy and AI"


class IeaHandler(PowerHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        links = html_extract.find_links(
            res.content, self.cfg.discovery.url,
            href_pattern=self.cfg.content.get("pdf_link_pattern", r"\.pdf$"))
        items, seen = [], set()
        for url, anchor in links:
            if url in seen or not url.lower().endswith(".pdf"):
                continue
            seen.add(url)
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=anchor or "IEA Energy and AI",
                url=url, doc_type="iea_report", chunking="pdf_sections",
                priority=1, is_pdf=True))
            if len(items) >= int(self.cfg.content.get("max_pdfs", 2)):
                break
        if not items:
            log.warning("[%s] no reachable IEA PDFs (may be account-gated); "
                        "skipping.", self.cfg.id)
        log.info("[%s] discovered %d IEA PDF(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        return self._ingest_pdf(
            item=item, publication=PUBLICATION, release_date=None,
            derive_date_if_missing=True,
            section_headings=self.cfg.content.get("section_headings"))
