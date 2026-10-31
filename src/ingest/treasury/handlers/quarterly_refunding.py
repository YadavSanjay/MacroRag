"""Endpoint 1 — Quarterly Refunding + TBAC (Ingestion Design §5.1).

The refunding is a mixed-format BUNDLE, not a single release. Stage A scrapes the
"Most Recent Documents" page and classifies each linked artifact by anchor text
via the config doc_type rules; the Auction/Buyback schedules are taken in XML
(preferred over the PDF mirror, §5.4). Stage B routes the XML schedules to the
time-series payload and the PDF reports (TBAC Report is highest-value) to the
vector store, sub-chunking the TBAC Report on its numbered "Charge" sections.
All artifacts share one ``refunding_quarter`` so the bundle is one logical event.
"""

from __future__ import annotations

from ...common import dateextract, feeds, html_extract, pdf_extract, structured
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import TreasuryHandler
from ..parsers import parse_refunding_statement, quarter_label

log = get_logger(__name__)

PUBLICATION = "Quarterly Refunding"
_SCHEDULE_TYPES = {"auction_schedule", "buyback_schedule"}


class QuarterlyRefundingHandler(TreasuryHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        prefer_xml = bool(self.cfg.content.get("prefer_xml", True))
        links = html_extract.find_links(res.content, self.cfg.discovery.url)

        items: list[DiscoveredItem] = []
        for url, anchor in links:
            doc_type, chunking, priority = feeds.classify(anchor, self.cfg.doc_type_rules)
            if doc_type == "unknown":
                continue
            low = url.lower()
            if doc_type in _SCHEDULE_TYPES:
                # Prefer the XML schedule; drop the PDF mirror when XML is wanted.
                if prefer_xml and low.endswith(".pdf"):
                    continue
                is_xml = low.endswith(".xml")
            else:
                is_xml = False
                if not low.endswith(".pdf"):
                    continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=anchor or doc_type, url=url,
                doc_type=doc_type, chunking=chunking, priority=priority,
                is_pdf=low.endswith(".pdf"), extra={"is_xml": is_xml},
            ))

        cap = int(self.cfg.content.get("max_artifacts", 14))
        items = items[:cap]
        log.info("[%s] discovered %d refunding artifact(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        quarter = quarter_label(self.ctx.today)

        if item.doc_type in _SCHEDULE_TYPES and item.extra.get("is_xml"):
            return self._fetch_schedule_xml(item, res, quarter)
        return self._fetch_pdf_artifact(item, res, quarter)

    # ── XML auction / buyback schedule -> time-series payload ────────────────

    def _fetch_schedule_xml(self, item, res, quarter) -> list[ParsedDocument]:
        records = structured.flatten_xml_records(res.content)
        table = "treasury_auctions" if item.doc_type == "auction_schedule" \
            else "treasury_buybacks"
        timeseries = [{"table": table, "refunding_quarter": quarter, **r}
                      for r in records]
        body = (f"{item.doc_type.replace('_', ' ').title()} ({quarter}): "
                f"{len(records)} scheduled operations.")
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            # synthetic index chunk for a schedule fetched now: stamp the crawl
            # date so the one-line chunk is still citable (there is no dateline).
            release_date=self.ctx.today, chunking="whole",
            fields={"artifact_type": item.doc_type, "refunding_quarter": quarter,
                    "record_count": len(records)},
            timeseries=timeseries,
        )
        return [doc] if doc else []

    # ── PDF artifacts (financing estimate, policy stmt, TBAC report/minutes) ──

    def _fetch_pdf_artifact(self, item, res, quarter) -> list[ParsedDocument]:
        with_tables = item.doc_type in ("tbac_charts", "financing_estimate")
        pages = pdf_extract.extract_pages(res.content, with_tables=with_tables)
        body = "\n\n".join(p.text for p in pages if p.text)

        fields = {"artifact_type": item.doc_type, "refunding_quarter": quarter,
                  "page_count": len(pages)}
        if item.doc_type in ("policy_statement", "financing_estimate"):
            fields.update(parse_refunding_statement(body))

        section_headings = (self.cfg.content.get("section_headings")
                            if item.doc_type in ("tbac_report", "tbac_minutes") else None)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            release_date=(
                dateextract.extract_date_from_text(body, max_date=self.ctx.today)
                or dateextract.extract_date_from_pdf(res.content, max_date=self.ctx.today)),
            chunking=item.chunking,
            section_headings=section_headings, fields=fields,
        )
        return [doc] if doc else []
