"""Tier 4 ingestor — wires the international handler registry onto the driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class IntlIngestor(TierIngestor):
    """Drives the IMF / BIS / BoJ endpoints (Ingestion Design §7)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
