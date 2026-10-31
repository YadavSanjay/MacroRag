"""Tier 4 ingestion — International Official Institutions.

Implements the five-endpoint blueprint in Ingestion Design §7: BIS Quarterly
Review, BIS Annual Economic Report, IMF WEO/GFSR, BoJ Outlook Report (PDF, via
URL-construction discovery), plus the IMF SDMX 3.0 API (numeric -> time-series).
Public entry point: :class:`IntlIngestor`.
"""

from __future__ import annotations

from .ingestor import IntlIngestor

__all__ = ["IntlIngestor"]
