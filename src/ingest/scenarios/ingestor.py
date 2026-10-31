"""Tier 7 ingestor — wires the scenario handler registry onto the driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class ScenariosIngestor(TierIngestor):
    """Drives the cross-asset stress & geopolitical-scenario endpoints (Tier 7)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
