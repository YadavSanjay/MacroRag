"""Tier 7 ingestion package — cross-asset stress & geopolitical scenarios.

The forward-looking *scenario* layer: authoritative, dated, quantified cross-asset
shock evidence (regulator stress-test scenarios, official geopolitical-risk
studies, think-tank tail-risk analyses) that the shock-vector synthesizer can
cite to anchor magnitudes for events the numeric corpus does not price directly
(e.g. a Taiwan-conflict tail). Unlike a feed, each document lives at a stable,
individually-curated URL, so the tier is driven by the ``pinned_docs`` handler.
"""

from __future__ import annotations

from .ingestor import ScenariosIngestor

__all__ = ["ScenariosIngestor"]
