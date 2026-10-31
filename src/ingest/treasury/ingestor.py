"""Tier 2 ingestor — wires the Treasury handler registry onto the generic driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class TreasuryIngestor(TierIngestor):
    """Drives the three U.S. Treasury endpoints (Ingestion Design §5)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
