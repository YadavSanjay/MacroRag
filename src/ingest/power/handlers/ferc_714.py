"""FERC Form 714 (Ingestion Design §9A.5).

The annual demand FORECAST is the unique add over EIA's realized region-data.
Filings are XBRL since 2021 (CSV before); robust XBRL parsing is explicitly
deferred to PUDL (catalyst.coop) per the blueprint. This stage discovers the
annual ZIP, persists it raw, and emits an index document so the archive is
tracked; the numeric ETL is the downstream loader's job.
"""

from __future__ import annotations

from ...common import html_extract
from ...common.http_client import sha256_hex
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, FetchResult, ParsedDocument
from ..common_base import PowerHandler

log = get_logger(__name__)

PUBLICATION = "FERC Form 714"


class Ferc714Handler(PowerHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_or_render(self.cfg.discovery.url)
        if res is None:
            log.warning("[%s] FERC 714 page unavailable (bot-walled); skipping.",
                        self.cfg.id)
            return []
        links = html_extract.find_links(
            res.content, self.cfg.discovery.url,
            href_pattern=self.cfg.content.get("zip_link_pattern", r"\.zip$"))
        items, seen = [], set()
        for url, anchor in links:
            if url in seen:
                continue
            seen.add(url)
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=anchor or "FERC Form 714 archive",
                url=url, doc_type="ferc_714_archive", chunking="whole",
                priority=1))
            if len(items) >= int(self.cfg.content.get("max_zips", 1)):
                break
        log.info("[%s] discovered %d FERC 714 ZIP(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        from ...common import structured
        members = structured.list_zip_members(res.content)
        body = ("FERC Form 714 annual archive. XBRL parsing deferred to PUDL "
                "(catalyst.coop). Members:\n" + "\n".join(members))
        # Reuse the already-fetched bytes; build via a synthetic result.
        synth = FetchResult(url=item.url, status_code=200, content=res.content,
                            content_type="application/zip",
                            content_hash=res.content_hash or sha256_hex(res.content))
        doc = self._build_document(
            item=item, res=synth, body_text=body, publication=PUBLICATION,
            release_date=None, chunking="whole",
            fields={"archive_members": members,
                    "note": "XBRL ETL deferred to PUDL parsers (§9A.5)"})
        return [doc] if doc else []
