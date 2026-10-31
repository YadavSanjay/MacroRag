"""Per-endpoint Fed handlers (Ingestion Design §4.1–§4.5).

Each handler implements the :class:`~ingest.common.base.EndpointHandler`
Stage A / Stage B contract for one Federal Reserve source. ``HANDLERS`` maps the
``handler`` id used in ``tier1_fed.yaml`` to its class.
"""

from __future__ import annotations

from ..common_base import FedHandler
from .dfast import DfastHandler
from .financial_stability import FinancialStabilityHandler
from .press_monetary import PressMonetaryHandler
from .sloos import SloosHandler
from .speeches import SpeechesHandler

HANDLERS: dict[str, type[FedHandler]] = {
    "press_monetary": PressMonetaryHandler,
    "financial_stability": FinancialStabilityHandler,
    "sloos": SloosHandler,
    "dfast": DfastHandler,
    "speeches": SpeechesHandler,
}

__all__ = [
    "HANDLERS",
    "FedHandler",
    "PressMonetaryHandler",
    "FinancialStabilityHandler",
    "SloosHandler",
    "DfastHandler",
    "SpeechesHandler",
]
