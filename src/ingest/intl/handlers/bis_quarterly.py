"""Endpoint 3 — BIS Quarterly Review (Ingestion Design §7.3).

The full issue sits at a deterministic URL, ``qr-{YYYYMM}.pdf`` (bis.org moved
from ``r_qt{YYMM}.pdf`` in 2026; the old URLs 301 to an HTML page). Constituent
article PDFs are slug-named (``<slug>_1.pdf``), so they are discovered by
scraping the issue page for its article pages and each article page for its
PDF. Discovery probes the actual release months (Mar/Sep/Dec), newest first.
"""

from __future__ import annotations

from datetime import date

import httpx

from ...common import html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import IntlHandler
from ..parsers import recent_issue_yymm

log = get_logger(__name__)

PUBLICATION = "BIS Quarterly Review"


class BisQuarterlyHandler(IntlHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        items: list[DiscoveredItem] = []
        issues = 0
        for cand in recent_issue_yymm(self.ctx.today, c.get("issue_months", [3, 9, 12]),
                                      int(c.get("years_back", 1))):
            yyyymm = f"{cand['year']}{cand['month']:02d}"
            full_url = c["full_url_pattern"].replace("{YYYYMM}", yyyymm)
            if not self._exists(full_url):
                continue
            rd = date(cand["year"], cand["month"], 1)
            items.append(self._item(full_url, "bis_quarterly_full", cand["label"], rd))
            for art_url, title in self._article_pdfs(yyyymm):
                items.append(self._item(art_url, "bis_quarterly_article",
                                        f"{cand['label']} — {title}", rd))
            issues += 1
            if issues >= int(c.get("max_issues", 1)):
                break
        log.info("[%s] discovered %d BIS QR artifact(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        return self._ingest_pdf(
            item=item, publication=PUBLICATION,
            release_date=item.extra.get("release_date"),
            section_headings=self.cfg.content.get("section_headings"),
            extra_fields={"artifact_type": item.doc_type},
        )

    def _article_pdfs(self, yyyymm: str) -> list[tuple[str, str]]:
        """``(pdf_url, title)`` for each article listed on the issue page.

        Best-effort: an unreachable page drops the articles, not the full issue.
        """
        c = self.cfg.content
        issue_url = c["issue_page_pattern"].replace("{YYYYMM}", yyyymm)
        page = self._get_page(issue_url)
        if page is None:
            return []
        articles = html_extract.find_links(
            page, issue_url,
            href_pattern=c["article_link_pattern"].replace("{YYYYMM}", yyyymm))
        out: list[tuple[str, str]] = []
        for art_url, anchor in articles[:int(c.get("max_articles", 8))]:
            art_page = self._get_page(art_url)
            if art_page is None:
                continue
            # The article's own PDF, not a link back to the full-issue qr-YYYYMM.pdf.
            pdfs = html_extract.find_links(art_page, art_url,
                                           href_pattern=r"(?<!qr-\d{6})\.pdf$")
            if pdfs:
                out.append((pdfs[0][0], anchor or art_url.rsplit("/", 1)[-1]))
        return out

    def _get_page(self, url: str) -> bytes | None:
        # Uncached: a 304 would hide articles a previous failed run never parsed.
        try:
            res = self.ctx.client.get(url, use_cache=False)
        except httpx.HTTPError as exc:
            log.warning("[%s] BIS page %s unavailable: %s", self.cfg.id, url, exc)
            return None
        return res.content if res.ok else None

    def _item(self, url: str, doc_type: str, label: str, rd: date) -> DiscoveredItem:
        return DiscoveredItem(
            source_id=self.cfg.id, title=f"{PUBLICATION} — {label}", url=url,
            doc_type=doc_type, chunking="pdf_sections", priority=1, is_pdf=True,
            extra={"release_date": rd},
        )
