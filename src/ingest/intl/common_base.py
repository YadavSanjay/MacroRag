"""Shared base for Tier 4 (international institution) handlers.

The four PDF endpoints (BIS QR/AER, IMF reports, BoJ) all do the same Stage B:
fetch a PDF, extract text (+optionally tables), chunk on section headings, route
tables to the time-series payload. :meth:`_ingest_pdf` centralises that. They
also share URL-construction discovery, so :meth:`_exists` wraps the polite HEAD
probe. The IMF SDMX handler subclasses this too but overrides discover/fetch.
"""

from __future__ import annotations

from ..common.handler_base import BaseSourceHandler
from ..common.logging_utils import get_logger

log = get_logger(__name__)


class IntlHandler(BaseSourceHandler):
    """Base for the five Tier-4 endpoint handlers.

    PDF ingestion (:meth:`_ingest_pdf`) now lives in the shared
    :class:`BaseSourceHandler`; Tier 4 only adds the HEAD-probe used by its
    URL-construction discovery.
    """

    def _exists(self, url: str) -> bool:
        """Polite HEAD probe for URL-construction discovery."""
        return self.ctx.client.head(url) == 200
