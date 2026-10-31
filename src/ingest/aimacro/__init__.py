"""Tier 6C ingestion — AI Macro / Productivity / Geopolitics.

Implements Ingestion Design §9C: GPR + EPU index downloads (pure numeric ->
time-series), Census BTOS (AI-adoption survey), and the Stanford HAI AI Index
(annual narrative PDF). Entry point: :class:`AiMacroIngestor`.
"""

from __future__ import annotations

from .ingestor import AiMacroIngestor

__all__ = ["AiMacroIngestor"]
