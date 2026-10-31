"""Per-endpoint Treasury handlers (Ingestion Design §5.1–§5.3).

``HANDLERS`` maps the ``handler`` id used in ``tier2_treasury.yaml`` to its class.
"""

from __future__ import annotations

from ..common_base import TreasuryHandler
from .oisp import OispHandler
from .quarterly_refunding import QuarterlyRefundingHandler
from .tic import TicHandler

HANDLERS: dict[str, type[TreasuryHandler]] = {
    "tic": TicHandler,
    "oisp": OispHandler,
    "quarterly_refunding": QuarterlyRefundingHandler,
}

__all__ = [
    "HANDLERS", "TreasuryHandler",
    "TicHandler", "OispHandler", "QuarterlyRefundingHandler",
]
