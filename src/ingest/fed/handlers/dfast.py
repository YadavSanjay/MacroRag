"""Endpoint 4 — DFAST supervisory stress test (Ingestion Design §4.4).

Two-hop discovery: the landing page is a hub with no artifacts; the files live on
a per-year page (``dfa-stress-tests-{YYYY}.htm``). From that page we ingest two
kinds of artifact:

* the scenarios **PDF** -> narrative chunks (methodology + scenario narrative);
* the Fed's companion scenario **CSVs** -> exact numeric shock paths for the
  time-series store (one file per scenario x domain). The CSV is the structured
  source of truth, so we no longer parse the dense PDF scenario tables.

Anchor text on the year page is mostly just "PDF", so classification keys off the
file URL, not the anchor. Bucket B: latest year (preferring the Final vintage).
"""

from __future__ import annotations

from ...common import dateextract, feeds, html_extract, pdf_extract, spreadsheet
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from .. import dfast_scenarios
from ..common_base import FedHandler
from ..parsers import date_from_url

log = get_logger(__name__)

PUBLICATION = "Dodd-Frank Act Stress Test"
DEFAULT_MAX_ARTIFACTS = 6


class DfastHandler(FedHandler):

    def discover(self) -> list[DiscoveredItem]:
        year_url = self._latest_year_page()
        if year_url is None:
            log.warning("[%s] no DFAST year page found; skipping.", self.cfg.id)
            return []
        res = self.ctx.client.get(year_url, use_cache=not self.ctx.refresh)
        if res.not_modified or not res.ok:
            return []

        items = self._pdf_items(res.content, year_url)
        if self.cfg.content.get("ingest_scenario_csv", True):
            items += self._scenario_csv_items(res.content, year_url)
        log.info("[%s] discovered %d DFAST artifact(s) from %s",
                 self.cfg.id, len(items), year_url.rsplit("/", 1)[-1])
        return items

    # ── discovery: PDFs (narrative) ──────────────────────────────────────────

    def _pdf_items(self, html: bytes, year_url: str) -> list[DiscoveredItem]:
        # Anchor text is unreliable ("PDF"); classify by the PDF URL instead.
        links = html_extract.find_links(html, year_url, href_pattern=r"\.pdf$")
        items: list[DiscoveredItem] = []
        for url, anchor in links:
            doc_type, chunking, priority = feeds.classify(url, self.cfg.doc_type_rules)
            if doc_type == "unknown":
                continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=anchor or doc_type, url=url,
                doc_type=doc_type, chunking=chunking or "pdf_sections",
                priority=priority, is_pdf=True,
            ))
        items.sort(key=lambda it: (date_from_url(it.url) or _min_date()), reverse=True)
        cap = int(self.cfg.content.get("max_artifacts", DEFAULT_MAX_ARTIFACTS))
        return items[:cap]

    # ── discovery: scenario CSVs (numeric) ───────────────────────────────────

    def _scenario_csv_items(self, html: bytes, year_url: str) -> list[DiscoveredItem]:
        links = html_extract.find_links(html, year_url, href_pattern=r"\.csv$")
        parsed = []
        for url, _ in links:
            name = url.rsplit("/", 1)[-1]
            meta = dfast_scenarios.parse_filename(name)
            if meta:
                parsed.append((url, name, meta))
        if not parsed:
            return []
        # The Final vintage supersedes the earlier Proposed set; pick one vintage.
        vintages = {m["vintage"] for _, _, m in parsed}
        prefer = "final" if "final" in vintages else next(iter(vintages))
        items: list[DiscoveredItem] = []
        for url, name, meta in parsed:
            if meta["vintage"] != prefer:
                continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=name, url=url,
                doc_type="dfast_scenario_data", chunking="whole",
                priority=1, is_pdf=False, extra=meta,
            ))
        return items

    def _latest_year_page(self) -> str | None:
        """Find the current per-year DFAST page via URL construction (newest first)."""
        pattern = self.cfg.content.get(
            "year_page_pattern",
            "https://www.federalreserve.gov/supervisionreg/dfa-stress-tests-{YYYY}.htm")
        years_back = int(self.cfg.content.get("years_back", 1))
        for year in range(self.ctx.today.year, self.ctx.today.year - years_back - 1, -1):
            url = pattern.replace("{YYYY}", str(year))
            if self.ctx.client.head(url) == 200:
                return url
        return None

    # ── content fetch ────────────────────────────────────────────────────────

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        if item.doc_type == "dfast_scenario_data":
            return self._fetch_scenario_csv(item)
        return self._fetch_scenario_pdf(item)

    def _fetch_scenario_csv(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        grid = spreadsheet.read_csv(res.content)
        meta = item.extra
        source = f"DFAST_{meta['year']}"
        timeseries = dfast_scenarios.build_timeseries(grid, source=source, meta=meta)
        if not timeseries:
            log.warning("[%s] no scenario rows parsed from %s", self.cfg.id, item.title)
            return []
        fields = {
            "scenario_set": source,
            "vintage": meta["vintage"],
            "scenario": meta["scenario"],
            "domain": meta["domain"],
            "variable_count": len({r["variable"] for r in timeseries}),
            "period_count": len({r["period"] for r in timeseries}),
        }
        doc = self._build_document(
            item=item, res=res, body_text="",
            publication=f"{PUBLICATION} — supervisory scenario data",
            release_date=None, chunking="whole", fields=fields,
            timeseries=timeseries,
        )
        return [doc] if doc else []

    def _fetch_scenario_pdf(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        pages = pdf_extract.extract_pages(res.content)   # narrative only (numbers via CSV)
        body = "\n\n".join(p.text for p in pages if p.text)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            # the methodology PDF URL has no date; fall back to the PDF dateline.
            release_date=(date_from_url(item.url)
                          or dateextract.extract_date_from_text(body, max_date=self.ctx.today)
                          or dateextract.extract_date_from_pdf(res.content, max_date=self.ctx.today)),
            chunking="pdf_sections",
            fields={"artifact_type": item.doc_type, "page_count": len(pages)},
        )
        return [doc] if doc else []


def _min_date():
    from datetime import date
    return date.min
