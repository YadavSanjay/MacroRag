"""Tier 1 ingestor — wires the Fed handler registry onto the generic driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class FedIngestor(TierIngestor):
    """Drives the five Federal Reserve endpoints (Ingestion Design §4)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
