"""Per-endpoint Tier 5 handlers (Ingestion Design §8.1–§8.5).

``HANDLERS`` maps the ``handler`` id used in ``tier5_house.yaml`` to its class.
"""

from __future__ import annotations

from ..common_base import HouseHandler
from .apollo import ApolloHandler
from .blackrock import BlackRockHandler
from .gs_tom import GsTomHandler
from .jpm_eotm import JpmEotmHandler
from .ms_totm import MsTotmHandler

HANDLERS: dict[str, type[HouseHandler]] = {
    "apollo": ApolloHandler,
    "ms_totm": MsTotmHandler,
    "gs_tom": GsTomHandler,
    "blackrock": BlackRockHandler,
    "jpm_eotm": JpmEotmHandler,
}

__all__ = [
    "HANDLERS", "HouseHandler", "ApolloHandler", "MsTotmHandler",
    "GsTomHandler", "BlackRockHandler", "JpmEotmHandler",
]
