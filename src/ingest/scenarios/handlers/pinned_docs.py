"""Pinned scenario / stress-test document downloads (Tier 7).

Cross-asset stress scenarios and geopolitical tail-risk studies live at stable,
individually-curated URLs (regulator stress-test PDFs, official geopolitical-risk
chapters, think-tank scenario reports). Unlike a feed there is no index to
scrape, and — critically for the citation gate — each document's *release date*
is authoritative and known up front, so it is pinned in the tier config's
``content.files[]`` alongside the publication label. Every file is downloaded and
run through the shared PDF (``_ingest_pdf``) or HTML (``_build_document``)
pipeline, emerging as dated, citable chunks the shock-vector synthesizer can
anchor magnitudes to.

Each ``files[]`` entry accepts:
    url             (required) content URL to fetch
    publication     grounding label (defaults to the endpoint title)
    release_date    ISO ``YYYY-MM-DD`` — REQUIRED for the chunk to pass the gate
    doc_type        per-document type tag (defaults to ``<id>_scenario``)
    is_pdf          override PDF detection (defaults to a ``.pdf`` URL suffix)
    section_headings optional heading list for section-aware chunking
    scenario_family optional grouping tag surfaced in ``fields``
    priority        discovery priority (1 = highest)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ...common import html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import ScenariosHandler

log = get_logger(__name__)


def _parse_date(value: str | None) -> date | None:
    """Parse a pinned ISO date; warn (and leave undated) on a malformed value."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        log.warning("scenarios: bad release_date %r; leaving undated", value)
        return None


def _is_pdf(entry: dict) -> bool:
    if "is_pdf" in entry:
        return bool(entry["is_pdf"])
    return entry["url"].lower().split("?")[0].endswith(".pdf")


class PinnedDocsHandler(ScenariosHandler):
    """Ingest each pinned document in ``content.files[]`` (PDF or HTML)."""

    def discover(self) -> list[DiscoveredItem]:
        items: list[DiscoveredItem] = []
        for entry in self.cfg.content.get("files", []):
            is_pdf = _is_pdf(entry)
            items.append(DiscoveredItem(
                source_id=self.cfg.id,
                title=entry.get("publication", entry["url"]),
                url=entry["url"],
                doc_type=entry.get("doc_type", f"{self.cfg.id}_scenario"),
                chunking="pdf_sections" if is_pdf else "sections",
                priority=int(entry.get("priority", 1)),
                is_pdf=is_pdf,
                extra={"file": entry},
            ))
        log.info("[%s] %d pinned document(s) to ingest", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        entry = item.extra["file"]
        publication = entry.get("publication", self.cfg.title)
        release_date = _parse_date(entry.get("release_date"))
        if release_date is None:
            log.warning("[%s] %s has no valid release_date — chunks will not pass "
                        "the citation gate", self.cfg.id, item.url)
        if item.is_pdf:
            return self._ingest_pdf(
                item=item, publication=publication, release_date=release_date,
                section_headings=entry.get("section_headings"),
                extra_fields={"scenario_family": entry.get("scenario_family")},
            )
        return self._ingest_html(item, publication, release_date)

    def _ingest_html(self, item: DiscoveredItem, publication: str,
                     release_date: date | None) -> list[ParsedDocument]:
        """Fetch an article page (with bot-wall render fallback) and build a doc."""
        res = self._fetch_or_render(item.url)
        if res is None or not res.content:
            return []
        entry = item.extra["file"]
        if not self._persist_if_new(item.url, res):
            return []
        body = html_extract.extract_article(res.content)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=publication,
            release_date=release_date, chunking="sections",
            section_headings=entry.get("section_headings"),
            fields={"scenario_family": entry.get("scenario_family")},
        )
        return [doc] if doc else []

    def _persist_if_new(self, url: str, res) -> bool:
        """Save raw bytes + enforce content idempotency for the render path
        (``_ingest_pdf``/``_fetch_content`` already do this for the GET path)."""
        from ...common.http_client import sha256_hex
        from ...common.storage import filename_from_url, safe_name

        if not res.content_hash:
            res.content_hash = sha256_hex(res.content)
        if self.ctx.cache.is_processed(url, res.content_hash):
            log.debug("[%s] unchanged, skipping: %s", self.cfg.id, url)
            return False
        self.ctx.raw_store.save(self.tier.tier, self.cfg.id,
                                safe_name(filename_from_url(url)), res.content)
        return True
