"""Per-endpoint Tier 6C handlers (Ingestion Design §9C)."""

from __future__ import annotations

from ..common_base import AiMacroHandler
from .census_btos import CensusBtosHandler
from .index_download import IndexDownloadHandler
from .stanford_hai import StanfordHaiHandler

HANDLERS: dict[str, type[AiMacroHandler]] = {
    "index_download": IndexDownloadHandler,   # GPR + EPU
    "census_btos": CensusBtosHandler,
    "stanford_hai": StanfordHaiHandler,
}

__all__ = ["HANDLERS", "AiMacroHandler", "IndexDownloadHandler",
           "CensusBtosHandler", "StanfordHaiHandler"]
