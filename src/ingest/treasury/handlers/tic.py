"""Endpoint 3 — TIC (Ingestion Design §5.3).

Discovery is deterministic: the monthly press notice lives at a predictable URL
(``Press-notice-TIC-for-{MMM}-{YYYY}.pdf``), so Stage A constructs the candidate
URLs for the last few release months and HEAD-probes them. Stage B extracts the
press-notice headline fields (data period, net TIC flow, next release) as the
narrative payload; the structured ZIP archive (Major Foreign Holders table) is
located on the landing page and persisted for the time-series loader.
"""

from __future__ import annotations

from ...common import dateextract, html_extract, pdf_extract, structured
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import TreasuryHandler
from ..parsers import expected_tic_urls, parse_tic_press_notice

log = get_logger(__name__)

PUBLICATION = "TIC Monthly Press Notice"


class TicHandler(TreasuryHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        items: list[DiscoveredItem] = []

        # 1) Predictable press-notice URLs for recent release months.
        candidates = expected_tic_urls(
            c.get("press_notice_dir", ""), c.get("press_notice_pattern", ""),
            self.ctx.today, int(c.get("probe_months_back", 4)),
        )
        for url, label in candidates:
            if self.ctx.client.head(url) == 200:
                items.append(DiscoveredItem(
                    source_id=self.cfg.id, title=f"TIC press notice — {label}",
                    url=url, doc_type="tic_press_notice", chunking="whole",
                    priority=1, is_pdf=True, extra={"release_month": label},
                ))

        # 2) Best-effort: newest data ZIP from the landing page (numeric backbone).
        zip_item = self._latest_data_zip()
        if zip_item:
            items.append(zip_item)

        log.info("[%s] discovered %d TIC artifact(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        if item.doc_type == "tic_data_archive":
            return self._fetch_archive(item)
        return self._fetch_press_notice(item)

    # ── press notice (narrative + headline fields) ───────────────────────────

    def _fetch_press_notice(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        body = pdf_extract.extract_text(res.content)
        fields = parse_tic_press_notice(body)
        fields.update(item.extra)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            release_date=(
                dateextract.extract_date_from_text(body, max_date=self.ctx.today)
                or dateextract.extract_date_from_pdf(res.content, max_date=self.ctx.today)),
            chunking="whole", fields=fields,
        )
        return [doc] if doc else []

    # ── structured ZIP archive (-> time-series loader) ───────────────────────

    def _latest_data_zip(self) -> DiscoveredItem | None:
        # Best-effort only: a broken/moved landing page must not kill the
        # deterministic press-notice path, so swallow HTTP errors here.
        import httpx

        try:
            res = self.ctx.client.get(self.cfg.landing_url or self.cfg.discovery.url)
        except httpx.HTTPError as exc:
            log.warning("[%s] data-archive landing fetch failed: %s", self.cfg.id, exc)
            return None
        if res.not_modified or not res.ok:
            return None
        links = html_extract.find_links(
            res.content, self.cfg.landing_url or self.cfg.discovery.url,
            href_pattern=self.cfg.content.get("data_file_pattern", r"\.zip$"),
        )
        if not links:
            return None
        url = links[0][0]
        return DiscoveredItem(
            source_id=self.cfg.id, title="TIC monthly data archive", url=url,
            doc_type="tic_data_archive", chunking="whole", priority=2, is_pdf=False,
        )

    def _fetch_archive(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        members = structured.list_zip_members(res.content)
        # Persist extracted members alongside the raw ZIP for the ts loader.
        dest = self.ctx.raw_store.root / f"tier{self.tier.tier}" / self.cfg.id / "archive"
        written = structured.extract_zip_to(res.content, dest)
        fields = {
            "archive_members": members,
            "extracted_paths": [str(p) for p in written],
            "note": "structured TXT/HTML members -> time-series loader (§5.3 gotcha)",
        }
        # Emit a single index chunk so the archive is discoverable in retrieval.
        body = "TIC monthly data archive members:\n" + "\n".join(members)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication="TIC Monthly Data Archive",
            # index chunk for the current archive snapshot: stamp the crawl date.
            release_date=self.ctx.today, chunking="whole", fields=fields,
        )
        return [doc] if doc else []
