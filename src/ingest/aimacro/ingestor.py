"""Tier 6C ingestor — wires the AI-macro handler registry onto the driver."""

from __future__ import annotations

from ..common.base import IngestContext, TierIngestor
from .handlers import HANDLERS


class AiMacroIngestor(TierIngestor):
    """Drives the GPR / EPU / BTOS / Stanford-HAI endpoints (Ingestion Design §9C)."""

    handler_registry = dict(HANDLERS)

    def __init__(self, ctx: IngestContext):
        super().__init__(ctx)
