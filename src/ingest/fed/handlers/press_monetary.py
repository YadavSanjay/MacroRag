"""Endpoint 1 — ``press_monetary.xml`` (Ingestion Design §4.1).

Discovery feed covering FOMC statements, minutes, SEP/dot-plot and the MPR. The
RSS title is the routing key; doc_type comes from the config regex table. Stage B
extracts the HTML body, pulls the headline fields (target range, voters,
dissenters, forward guidance) for statements, sub-chunks minutes on their
section headings, and routes SEP projection tables to the time-series payload.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ...common import feeds, html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from .. import sep_projections
from ..common_base import FedHandler
from ..parsers import date_from_url, parse_fomc_statement

log = get_logger(__name__)

PUBLICATION = "FOMC Monetary Policy"
# The HTML minutes page a minutes press release links (not the PDF).
_MINUTES_HREF = r"/monetarypolicy/fomcminutes\d{8}\.htm"


class PressMonetaryHandler(FedHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        return feeds.parse_rss(res.content, self.cfg.id, self.cfg.doc_type_rules)

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []

        release_date = date_from_url(item.url) or _as_date(item.published)
        selectors = self.cfg.content.get("body_selectors")
        if item.doc_type == "fomc_minutes":
            # The release is a notice ("The minutes can be viewed on the Board's
            # website"); the text lives on the linked minutes page. Store nothing
            # rather than the notice, so a later run retries.
            minutes_url = minutes_page_url(res.content, item.url)
            if minutes_url is None:
                log.warning("[%s] minutes: no minutes page linked from %s",
                            self.cfg.id, item.url)
                return []
            res = self._fetch_content(minutes_url)
            if res is None:
                return []
        body = html_extract.extract_body(res.content, selectors)

        fields: dict = {}
        timeseries: list[dict] = []

        if item.doc_type == "fomc_statement":
            fields = parse_fomc_statement(body)
        elif item.doc_type == "sep_projections":
            # The press release is a thin wrapper; the clean projection tables
            # live on the linked HTML projections-materials page.
            timeseries = self._sep_timeseries(res.content, item.url, release_date)

        publication = _publication_for(item.doc_type)
        doc = self._build_document(
            item=item,
            res=res,
            body_text=body,
            publication=publication,
            release_date=release_date,
            chunking=item.chunking,
            section_headings=self.cfg.content.get("section_headings"),
            fields=fields,
            timeseries=timeseries,
        )
        return [doc] if doc else []

    # ── helpers ──────────────────────────────────────────────────────────────

    def _sep_timeseries(self, html_bytes: bytes, page_url: str,
                        release_date: date | None) -> list[dict]:
        """Follow the HTML projections page and parse its summary table to rows."""
        links = html_extract.find_links(html_bytes, page_url,
                                        href_pattern=r"fomcprojtabl\d+\.htm")
        if not links:
            log.warning("[%s] SEP: no projections page linked from %s",
                        self.cfg.id, page_url)
            return []
        proj_url = links[0][0]
        res = self._fetch_content(proj_url)
        if res is None:
            return []
        grids = html_extract.extract_tables(res.content)
        if not grids:
            return []
        source = f"SEP_{release_date:%Y-%m}" if release_date else "SEP"
        rows = sep_projections.parse_projection_table(
            grids[0], source=source, release=release_date or self.ctx.today)
        log.info("[%s] SEP: %d projection rows from %s",
                 self.cfg.id, len(rows), proj_url)
        return rows


def minutes_page_url(html: bytes | str, page_url: str) -> str | None:
    """The HTML FOMC minutes page linked from a minutes press release.

    Args:
        html: The press release page.
        page_url: The release page's URL, for resolving relative links.

    Returns:
        The absolute minutes URL, or None when the page links none.
    """
    links = html_extract.find_links(html, page_url, href_pattern=_MINUTES_HREF)
    return links[0][0] if links else None


def _as_date(value) -> date | None:
    return value.date() if value is not None else None


def _publication_for(doc_type: str) -> str:
    return {
        "fomc_statement": "FOMC Statement",
        "fomc_minutes": "FOMC Minutes",
        "sep_projections": "Summary of Economic Projections",
        "framework_reaffirmation": "Statement on Longer-Run Goals",
        "discount_rate_minutes": "Discount Rate Minutes",
    }.get(doc_type, PUBLICATION)
