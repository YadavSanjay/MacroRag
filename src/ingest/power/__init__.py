"""Tier 6A ingestion — Power & Data-Center Electricity.

Implements Ingestion Design §9A: EIA AEO/STEO forward forecasts (-> time-series),
the IEA Energy & AI report (PDF), FERC Form 714 (raw archive; XBRL parse deferred
to PUDL), and PJM Data Miner 2 (account-gated). Entry: :class:`PowerIngestor`.
"""

from __future__ import annotations

from .ingestor import PowerIngestor

__all__ = ["PowerIngestor"]
