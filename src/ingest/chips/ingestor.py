"""Tier 6B ingestor — wires the chips/policy handler registry onto the driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class ChipsIngestor(TierIngestor):
    """Drives the Federal Register / Entity List / TSMC / SIA endpoints (§9B)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
