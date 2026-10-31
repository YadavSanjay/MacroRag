"""Endpoint 3 — CBO (Ingestion Design §6.3).

No API/RSS, but stable URL patterns. Stage A scrapes the publications index for
``/publication/{ID}`` landing pages. Stage B prefers the dense Executive Summary
PDF over the 180+ page full report, sub-chunks on the Outlook's chapter
headings, and surfaces any supplemental XLSX/CSV (the 10-year projection tables —
the highest-value Tier-3 numeric output for the term-premium prior) for the
time-series loader. The 10Y-Treasury-yield projection is the key retrieval target.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import httpx

from ...common import html_extract, pdf_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, FetchResult, ParsedDocument
from ..common_base import UsStatsHandler
from ..parsers import classify_cbo_publication

log = get_logger(__name__)

PUBLICATION = "CBO Publication"
_PUB_ID = re.compile(r"/publication/(\d+)")
_FILE_DATE = re.compile(r"/system/files/(\d{4})-(\d{2})/")


class CboHandler(UsStatsHandler):

    def _safe_get(self, url: str) -> FetchResult | None:
        """GET with a headless-browser fallback; bot-walls degrade, not crash.

        cbo.gov sits behind a DataDome challenge. Plain HTTP gets a 403; we then
        try the shared headless browser. DataDome additionally fingerprints
        headless Chromium and serves the same challenge, so this still degrades
        to a no-op (BEA/BLS — the high-value numeric Tier-3 sources — are
        unaffected). Bypassing DataDome needs stealth/residential-proxy tooling
        out of scope here; the wiring is correct for when that lands.
        """
        try:
            res = self.ctx.client.get(url)
            if not (res.not_modified or not res.ok):
                return res
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (403, 429):
                raise
            log.info("[%s] %s returned %d; trying headless browser.",
                     self.cfg.id, url, exc.response.status_code)

        content = self._render(url)
        if content and not _is_bot_challenge(content):
            from ...common.http_client import sha256_hex
            return FetchResult(url=url, status_code=200, content=content,
                               content_type="text/html",
                               content_hash=sha256_hex(content))
        log.warning("[%s] %s still bot-walled (DataDome challenge survives "
                    "headless render); skipping.", self.cfg.id, url)
        return None

    def discover(self) -> list[DiscoveredItem]:
        res = self._safe_get(self.cfg.discovery.url)
        if res is None:
            return []
        links = html_extract.find_links(res.content, self.cfg.discovery.url,
                                        href_pattern=r"/publication/\d+")
        seen: set[str] = set()
        items: list[DiscoveredItem] = []
        for url, anchor in links:
            m = _PUB_ID.search(url)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=anchor or f"CBO publication {m.group(1)}",
                url=url, doc_type=classify_cbo_publication(anchor),
                chunking="pdf_sections", priority=1,
                extra={"publication_id": m.group(1)},
            ))
        cap = int(self.cfg.content.get("max_publications", 6))
        items = items[:cap]
        log.info("[%s] discovered %d CBO publication(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        landing = self._safe_get(item.url)
        if landing is None:
            return []
        c = self.cfg.content

        pdfs = html_extract.find_links(landing.content, item.url,
                                       href_pattern=c.get("pdf_link_pattern", r"\.pdf$"))
        data_files = html_extract.find_links(
            landing.content, item.url,
            href_pattern=c.get("data_file_pattern", r"\.(xlsx|xls|csv)$"))
        if not pdfs:
            log.info("[%s] no PDF on %s; skipping", self.cfg.id, item.url)
            return []

        chosen_url, is_exec = self._choose_pdf(pdfs, c.get("exec_summary_hint", ""))
        res = self._fetch_content(chosen_url)
        if res is None:
            return []

        body = pdf_extract.extract_text(res.content)
        fields = {
            "publication_id": item.extra["publication_id"],
            "publication_type": item.doc_type,
            "is_executive_summary": is_exec,
            "landing_url": item.url,
            "data_files": [u for u, _ in data_files],
        }
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            release_date=_date_from_file_url(chosen_url),
            chunking="pdf_sections",
            section_headings=c.get("section_headings"), fields=fields,
        )
        return [doc] if doc else []

    @staticmethod
    def _choose_pdf(pdfs: list[tuple[str, str]], exec_hint: str) -> tuple[str, bool]:
        """Prefer the Executive Summary PDF (dense) over the full report."""
        if exec_hint:
            for url, _ in pdfs:
                if exec_hint.lower() in url.lower():
                    return url, True
        return pdfs[0][0], False


def _is_bot_challenge(content: bytes) -> bool:
    """Detect a DataDome/JS-challenge interstitial vs the real page."""
    head = content[:2000].decode("utf-8", "ignore").lower()
    return ("datadome" in head or "enable js" in head
            or "please enable" in head or len(content) < 5000)


def _date_from_file_url(url: str) -> date | None:
    m = _FILE_DATE.search(url)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)}-{m.group(2)}-01", "%Y-%m-%d").date()
        except ValueError:
            return None
    return None
