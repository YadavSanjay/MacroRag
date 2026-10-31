"""Tier 3 ingestion — U.S. Statistical & Policy Agencies.

Implements the three native Tier-3 endpoints from Ingestion Design §6: BEA
(GET REST), BLS (POST REST), and CBO (HTML + PDF). BEA/BLS output is purely
numeric and lands in the time-series payload (no vector chunks); CBO populates
the vector store plus supplemental data references. Public entry point:
:class:`UsStatsIngestor`.
"""

from __future__ import annotations

from .ingestor import UsStatsIngestor

__all__ = ["UsStatsIngestor"]
