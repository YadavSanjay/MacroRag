"""Per-endpoint Tier 6A handlers (Ingestion Design §9A)."""

from __future__ import annotations

from ..common_base import PowerHandler
from .eia import EiaHandler
from .ferc_714 import Ferc714Handler
from .iea import IeaHandler
from .pjm import PjmHandler

HANDLERS: dict[str, type[PowerHandler]] = {
    "eia": EiaHandler,          # AEO + STEO
    "iea": IeaHandler,
    "ferc_714": Ferc714Handler,
    "pjm": PjmHandler,
}

__all__ = ["HANDLERS", "PowerHandler", "EiaHandler", "IeaHandler",
           "Ferc714Handler", "PjmHandler"]
