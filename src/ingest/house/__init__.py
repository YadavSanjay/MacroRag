"""Tier 5 ingestion — House Views (public flagships).

Implements the five-endpoint blueprint in Ingestion Design §8: Apollo Academy,
Morgan Stanley Thoughts on the Market, Goldman Sachs Top of Mind, BlackRock
Investment Institute, and JPM Eye on the Market. Every chunk records its licence
flag, which nothing enforces, with author/speaker attribution preserved at chunk
level. Entry point:
:class:`HouseIngestor`.
"""

from __future__ import annotations

from .ingestor import HouseIngestor

__all__ = ["HouseIngestor"]
