"""Tier 3 ingestor — wires the US-stats handler registry onto the generic driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class UsStatsIngestor(TierIngestor):
    """Drives the BEA / BLS / CBO endpoints (Ingestion Design §6)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
