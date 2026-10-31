"""Per-endpoint Tier 7 handlers (cross-asset stress & geopolitical scenarios)."""

from __future__ import annotations

from ..common_base import ScenariosHandler
from .pinned_docs import PinnedDocsHandler

HANDLERS: dict[str, type[ScenariosHandler]] = {
    "pinned_docs": PinnedDocsHandler,
}

__all__ = ["HANDLERS", "ScenariosHandler", "PinnedDocsHandler"]
