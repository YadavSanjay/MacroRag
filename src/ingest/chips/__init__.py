"""Tier 6B ingestion — Semiconductors, Export Controls & Trade Policy.

Implements Ingestion Design §9B: the Federal Register catalyst feed (BIS rules +
Section 232 proclamations — the dominant feed), the BIS Entity List, TSMC
monthly sales/capex, and SIA monthly global sales. Entry: :class:`ChipsIngestor`.
"""

from __future__ import annotations

from .ingestor import ChipsIngestor

__all__ = ["ChipsIngestor"]
