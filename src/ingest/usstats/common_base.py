"""Shared base for Tier 3 (U.S. statistical agencies) handlers.

Adds two things on top of :class:`BaseSourceHandler` that the REST endpoints
need and the HTML/PDF tiers did not:

* :meth:`_secret` — read the per-endpoint API key from the environment.
* :meth:`_finish_api_doc` — assemble a time-series-only document from an
  already-fetched API response, keyed on a *redacted* URL so the persisted JSON
  never contains the API key, with raw-bytes persistence + idempotency.
"""

from __future__ import annotations

from typing import Optional

from ..common.config import get_secret
from ..common.handler_base import BaseSourceHandler
from ..common.logging_utils import get_logger
from ..common.models import DiscoveredItem, FetchResult, ParsedDocument
from ..common.storage import safe_name

log = get_logger(__name__)


class UsStatsHandler(BaseSourceHandler):
    """Base for the BEA / BLS / CBO endpoint handlers."""

    def _secret(self, env_name: str | None) -> str | None:
        if not env_name:
            return None
        val = get_secret(env_name)
        if not val:
            log.error("[%s] missing API key env var %s", self.cfg.id, env_name)
        return val

    def _finish_api_doc(
        self,
        *,
        item: DiscoveredItem,
        redacted_url: str,
        raw_content: bytes,
        content_hash: str,
        publication: str,
        timeseries: list[dict],
        fields: dict,
        raw_name: str,
    ) -> ParsedDocument | None:
        """Persist raw bytes + assemble a numeric (chunk-free) time-series doc."""
        if not timeseries:
            log.warning("[%s] no observations parsed for %s", self.cfg.id, raw_name)
            return None
        if self.ctx.cache.is_processed(redacted_url, content_hash):
            log.debug("[%s] unchanged, skipping: %s", self.cfg.id, redacted_url)
            return None
        self.ctx.raw_store.save(self.tier.tier, self.cfg.id,
                                safe_name(raw_name), raw_content)
        # Synthetic FetchResult: body already consumed; URL is key-redacted.
        res = FetchResult(url=redacted_url, status_code=200,
                          content_hash=content_hash)
        return self._build_document(
            item=item, res=res, body_text="", publication=publication,
            release_date=None, chunking="whole", fields=fields,
            timeseries=timeseries,
        )
