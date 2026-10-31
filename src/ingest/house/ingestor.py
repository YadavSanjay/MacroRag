"""Tier 5 ingestor — wires the house-view handler registry onto the driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class HouseIngestor(TierIngestor):
    """Drives the five public house-view endpoints (Ingestion Design §8)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
