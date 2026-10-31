"""Endpoint 3 — SLOOS (Ingestion Design §4.3).

Discovery scrapes the survey landing page for ``sloos-YYYYMM.htm`` release pages,
newest first. (The ``feeds/sloos.xml`` RSS feed only carries Data Download
Program notices, all anchors on one page — never the survey itself.) A release is
a bundle: qualitative report (narrative -> vector store) plus numeric charts data
(CSV/XLSX -> time-series store). Stage B extracts the report narrative and
sub-chunks on loan-category sections; the numeric data files are surfaced as
links in ``fields`` for the downstream time-series loader (joins to FRED
``DRTSCILM`` / ``DRSDCIS``), keeping this stage narrative-first.
"""

from __future__ import annotations

import re

from ...common import dateextract, html_extract, pdf_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import FedHandler
from ..parsers import _as_date_or_none, date_from_last_update, date_from_url

log = get_logger(__name__)

PUBLICATION = "Senior Loan Officer Opinion Survey"

_SURVEY_MONTH = re.compile(r"sloos-(\d{6})\.htm")


def _survey_month(url: str) -> str:
    """``YYYYMM`` from a ``sloos-YYYYMM.htm`` URL ("" if absent), for sorting."""
    m = _SURVEY_MONTH.search(url)
    return m.group(1) if m else ""


_RELEASE_TITLE = re.compile(r"The \w+ \d{4} Senior Loan Officer Opinion Survey")


def _from_release_title(body: str) -> str:
    """Drop the download-link chrome above the release title, if the title is found.

    Args:
        body: Extracted page text.

    Returns:
        The text from ``The <Month> <Year> Senior Loan Officer Opinion Survey``
        onwards, or ``body`` unchanged when no such title is present.
    """
    m = _RELEASE_TITLE.search(body)
    return body[m.start():] if m else body


class SloosHandler(FedHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        links = html_extract.find_links(
            res.content, self.cfg.discovery.url,
            href_pattern=self.cfg.content.get("release_link_pattern",
                                              r"/sloos-\d{6}\.htm$"))
        links.sort(key=lambda link: _survey_month(link[0]), reverse=True)
        return [
            DiscoveredItem(source_id=self.cfg.id, title=anchor or PUBLICATION, url=url,
                           doc_type="sloos_release", chunking="sections", priority=1)
            for url, anchor in links[:int(self.cfg.content.get("max_releases", 4))]
        ]

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []

        is_pdf = item.is_pdf or "pdf" in res.content_type.lower()
        fields: dict = {}
        if is_pdf:
            body = pdf_extract.extract_text(res.content)
        else:
            body = _from_release_title(html_extract.extract_body(
                res.content, self.cfg.content.get("body_selectors")))
            # Surface the numeric data files for the time-series loader.
            data_links = html_extract.find_links(
                res.content, item.url, href_pattern=r"\.(csv|xlsx?|zip)$"
            )
            if data_links:
                fields["data_files"] = [u for u, _ in data_links]

        # The page footer is the publication date. The URL's YYYYMM is the survey
        # month, which precedes publication (July survey -> early August), so it is
        # only a last resort.
        release_date = (
            (None if is_pdf else date_from_last_update(res.content))
            or _as_date_or_none(item.published)
            or dateextract.extract_date_from_text(body, max_date=self.ctx.today)
            or date_from_url(item.url))
        doc = self._build_document(
            item=item,
            res=res,
            body_text=body,
            publication=PUBLICATION,
            release_date=release_date,
            chunking=item.chunking,
            section_headings=self.cfg.content.get("section_headings"),
            fields=fields,
        )
        return [doc] if doc else []
