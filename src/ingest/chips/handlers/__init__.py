"""Per-endpoint Tier 6B handlers (Ingestion Design §9B)."""

from __future__ import annotations

from ..common_base import ChipsHandler
from .bis_entity_list import BisEntityListHandler
from .federal_register import FederalRegisterHandler
from .sia import SiaHandler
from .tsmc import TsmcHandler

HANDLERS: dict[str, type[ChipsHandler]] = {
    "federal_register": FederalRegisterHandler,
    "bis_entity_list": BisEntityListHandler,
    "tsmc": TsmcHandler,
    "sia": SiaHandler,
}

__all__ = ["HANDLERS", "ChipsHandler", "FederalRegisterHandler",
           "BisEntityListHandler", "TsmcHandler", "SiaHandler"]
