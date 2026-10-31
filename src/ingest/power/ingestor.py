"""Tier 6A ingestor — wires the power handler registry onto the driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class PowerIngestor(TierIngestor):
    """Drives the EIA / IEA / FERC / PJM endpoints (Ingestion Design §9A)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
