"""Tier 1 ingestion — Federal Reserve System.

Implements the five-endpoint blueprint in Ingestion Design §4 on top of the
tier-agnostic ``ingest.common`` scaffold. The public entry point is
:class:`FedIngestor`; per-endpoint logic lives in :mod:`ingest.fed.handlers`
and Fed-specific field extraction in :mod:`ingest.fed.parsers`.
"""

from __future__ import annotations

from .ingestor import FedIngestor

__all__ = ["FedIngestor"]
