"""Per-endpoint Tier 3 handlers (Ingestion Design §6.1–§6.3).

``HANDLERS`` maps the ``handler`` id used in ``tier3_usstats.yaml`` to its class.
"""

from __future__ import annotations

from ..common_base import UsStatsHandler
from .acm import AcmDailyHandler
from .bea import BeaHandler
from .bls import BlsHandler
from .cbo import CboHandler
from .episodes import MarketEpisodeHandler
from .fred import FredDailyHandler

HANDLERS: dict[str, type[UsStatsHandler]] = {
    "bea": BeaHandler,
    "bls": BlsHandler,
    "cbo": CboHandler,
    "fred": FredDailyHandler,
    "acm": AcmDailyHandler,
    "episodes": MarketEpisodeHandler,
}

__all__ = ["HANDLERS", "UsStatsHandler", "BeaHandler", "BlsHandler", "CboHandler",
           "FredDailyHandler", "MarketEpisodeHandler"]
