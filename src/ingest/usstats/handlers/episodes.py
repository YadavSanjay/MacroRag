"""Market-episode cards (spec market-episodes).

Builds one card per configured episode from the daily FRED rows the
``fred_daily`` endpoint stored, read back from the processed-document store. It
makes no HTTP request, so it must run after ``fred_daily`` in the same tier.
"""

from __future__ import annotations

from typing import Any

from utils.objectstore import object_store_from_env

from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import UsStatsHandler
from ..episodes import DOC_TYPE, build_episode_document, read_episode_rows

log = get_logger(__name__)


class MarketEpisodeHandler(UsStatsHandler):
    """Episode cards computed from stored FRED daily series (spec market-episodes).

    Reads the rows the fred_daily endpoint stored and emits one card per configured
    episode, with no HTTP request.
    """

    _rows: list[dict[str, Any]] | None = None

    def discover(self) -> list[DiscoveredItem]:
        """One item per configured episode."""
        items = [DiscoveredItem(
            source_id=self.cfg.id, title=f"Market episode {e['name']}",
            url=f"episode://{e['id']}", doc_type=DOC_TYPE, chunking="whole",
            priority=1, extra={"episode": e})
            for e in self.cfg.content.get("episodes", [])]
        log.info("[%s] %d market episode(s) to build", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        """Build one episode's card from the stored FRED rows."""
        if self._rows is None:
            store = object_store_from_env(self.ctx.doc_store.root, "processed/ingest")
            self._rows = read_episode_rows(store)
        doc = build_episode_document(item.extra["episode"], self._rows,
                                     today=self.ctx.today)
        if doc is None:
            log.warning("[%s] %s: no FRED moves around the anchor; no card",
                        self.cfg.id, item.url)
            return []
        if self.ctx.cache.is_processed(doc.url, doc.content_hash):
            log.debug("[%s] unchanged, skipping: %s", self.cfg.id, doc.url)
            return []
        return [doc]
