"""Endpoint 2 — OISP (Ingestion Design §5.2).

Event-driven Taiwan-tail catalyst stream. Stage A has three watchers: the
Federal Register API (Treasury Office of Investment Security rules — the
high-value items), the program-regulations page (standalone guidance PDFs), and
the FAQ PDF (revised in place; re-ingested on body-hash change). Stage B prefers
the Federal Register ``body_html_url`` rendered HTML over the linked PDF and
stamps each chunk with effective date, covered sectors, and countries of concern.
"""

from __future__ import annotations

from ...common import dateextract, federal_register, html_extract, pdf_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import TreasuryHandler
from ..parsers import fr_publication_date, parse_oisp_document

log = get_logger(__name__)

PUBLICATION = "OISP / Federal Register Rule"


class OispHandler(TreasuryHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        items: list[DiscoveredItem] = []

        # 1) Federal Register rules (primary catalyst stream).
        conditions: dict = {}
        if c.get("federal_register_agency"):
            conditions["agencies"] = [c["federal_register_agency"]]
        if c.get("federal_register_term"):
            conditions["term"] = c["federal_register_term"]
        docs = federal_register.search(
            self.ctx.client, conditions=conditions,
            per_page=min(int(c.get("max_documents", 25)), 100), max_pages=1,
            use_cache=not self.ctx.refresh,
        )
        for doc in docs:
            # Prefer the clean raw-text endpoint over the chrome-heavy HTML body.
            url = doc.get("raw_text_url") or doc.get("body_html_url") or doc.get("html_url")
            if not url:
                continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id,
                title=doc.get("title", "Federal Register document"),
                url=url, doc_type="oisp_rule", chunking="sections", priority=1,
                is_pdf=False, extra={"fr_doc": doc},
            ))

        # 2) FAQ PDF watch (re-ingested only when the body hash changes).
        if c.get("faq_pdf_url"):
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title="OISP FAQ", url=c["faq_pdf_url"],
                doc_type="oisp_faq", chunking="paragraphs", priority=2, is_pdf=True,
            ))

        # 3) Program-regulations page — standalone guidance PDFs.
        items.extend(self._program_guidance_items())

        log.info("[%s] discovered %d OISP item(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        if item.doc_type == "oisp_rule":
            return self._fetch_rule(item)
        return self._fetch_pdf(item)

    # ── Federal Register rule (rendered HTML body) ───────────────────────────

    def _fetch_rule(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        # raw_text_url payload wraps the rule body in <pre>; otherwise it's the
        # rendered HTML body fragment.
        if item.url.endswith(".txt") or "/text/" in item.url:
            body = federal_register.pre_text(res.content)
        else:
            body = html_extract.extract_body(res.content)
        doc_meta = item.extra.get("fr_doc", {})
        fields = parse_oisp_document(doc_meta, body)
        rel = fr_publication_date(doc_meta)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            release_date=rel, chunking="sections", fields=fields,
        )
        return [doc] if doc else []

    # ── PDF items (FAQ + program guidance) ───────────────────────────────────

    def _fetch_pdf(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        body = pdf_extract.extract_text(res.content)
        fields = {"revision_seen": self.ctx.today.isoformat()} \
            if item.doc_type == "oisp_faq" else {}
        publication = "OISP FAQ" if item.doc_type == "oisp_faq" else "OISP Guidance"
        # Guidance PDFs carry a dateline; the FAQ is revised in place, so its
        # effective version date is the crawl. Fall back to today so the chunk is
        # citable either way.
        release_date = (
            dateextract.extract_date_from_text(body, max_date=self.ctx.today)
            or dateextract.extract_date_from_pdf(res.content, max_date=self.ctx.today)
            or self.ctx.today)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=publication,
            release_date=release_date, chunking=item.chunking, fields=fields,
        )
        return [doc] if doc else []

    def _program_guidance_items(self) -> list[DiscoveredItem]:
        url = self.cfg.content.get("program_regulations_url")
        if not url:
            return []
        res = self.ctx.client.get(url)
        if res.not_modified or not res.ok:
            return []
        links = html_extract.find_links(res.content, url, href_pattern=r"\.pdf$")
        return [
            DiscoveredItem(
                source_id=self.cfg.id, title=anchor or "OISP guidance", url=u,
                doc_type="oisp_guidance", chunking="pdf_sections", priority=3,
                is_pdf=True,
            )
            for u, anchor in links
        ]
