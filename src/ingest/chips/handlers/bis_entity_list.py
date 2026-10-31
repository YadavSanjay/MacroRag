"""BIS-Commerce Entity List (Ingestion Design §9B.2).

Cumulative state, not an event stream. This stage ingests the landing page as a
narrative snapshot and surfaces any structured export links (CSV/XML) for the
downstream loader to diff into a ``bis_entity_list`` table. Every Entity List
change is also a Federal Register rule, so the §9B.1 feed provides redundancy.
"""

from __future__ import annotations

from ...common import html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import ChipsHandler

log = get_logger(__name__)

PUBLICATION = "BIS Entity List"


class BisEntityListHandler(ChipsHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_or_render(self.cfg.discovery.url)
        if res is None:
            return []
        export_links = html_extract.find_links(
            res.content, self.cfg.discovery.url,
            href_pattern=self.cfg.content.get("export_link_pattern", r"\.(csv|xml)$"))
        return [DiscoveredItem(
            source_id=self.cfg.id, title="BIS Entity List (snapshot)",
            url=self.cfg.discovery.url, doc_type="bis_entity_list",
            chunking="whole", priority=1,
            extra={"export_links": [u for u, _ in export_links],
                   "_rendered": res.content},
        )]

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        # Reuse the page already fetched in discovery to avoid a second request.
        from ...common.http_client import sha256_hex
        from ...common.models import FetchResult

        content = item.extra["_rendered"]
        body = html_extract.extract_article(content)
        chash = sha256_hex(content)
        if self.ctx.cache.is_processed(item.url, chash):
            return []
        self.ctx.raw_store.save(self.tier.tier, self.cfg.id,
                                "entity_list_snapshot.html", content)
        res = FetchResult(url=item.url, status_code=200, content=content,
                          content_type="text/html", content_hash=chash)
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            # Cumulative state with no intrinsic publication date: the snapshot is
            # as-of the crawl, so stamp the retrieval date. Without a release_date
            # the narrative chunk cannot pass the synthesis citation gate.
            release_date=self.ctx.today, chunking="whole",
            fields={"export_links": item.extra.get("export_links", []),
                    "snapshot_date": self.ctx.today.isoformat(),
                    "note": "cumulative state; loader diffs into bis_entity_list table"})
        return [doc] if doc else []
