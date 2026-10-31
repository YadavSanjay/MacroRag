"""Tier 2 ingestion — U.S. Treasury.

Implements the three-endpoint blueprint in Ingestion Design §5 (TIC, OISP,
Quarterly Refunding + TBAC) on top of the tier-agnostic ``ingest.common``
scaffold. Public entry point: :class:`TreasuryIngestor`.
"""

from __future__ import annotations

from .ingestor import TreasuryIngestor

__all__ = ["TreasuryIngestor"]
