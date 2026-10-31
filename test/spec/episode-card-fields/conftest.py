"""Fixtures for the episode-card-fields spec tests.

Turns the repo dotenv file off before anything imports from ``src`` and puts the repo ``src``
on ``sys.path``. The tests call committed pure code only (``synthesis.diagnostics``,
``ingest.usstats.episodes``, ``synthesis.vocab``): no fakes, no network, no ``data/``, no clock.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

# Before any import from src: never load the repo's secrets dotenv file.
os.environ["MACRORAG_DOTENV"] = "0"

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# The 11 card fields, copied from the spec (Requirement, C1, Notes); not read from the code.
CARD_FIELDS = frozenset({"VIX", "UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "IG_OAS",
                         "HY_OAS", "USDTWD", "USDJPY", "REAL_YIELD_10Y",
                         "TERM_PREMIUM_10Y"})
# The vocabulary fields no card carries, copied from spec C1.
NON_CARD_FIELDS = frozenset({"UST", "MOVE", "MOVE_1Y", "MOVE_5Y", "MOVE_10Y", "MOVE_30Y",
                             "EQUITY_US", "XCCY_BASIS"})
NEW_FIELDS = ("REAL_YIELD_10Y", "TERM_PREMIUM_10Y")


def entry(name: str, reason: str = "not_emitted", codes: list[str] | None = None) -> dict:
    """The missing_fields entry the spec expects for ``name`` (card_field from the literal)."""
    return {"field": name, "reason": reason, "codes": list(codes or []),
            "card_field": name in CARD_FIELDS}


@pytest.fixture(scope="session")
def ecf():
    """Namespace of the constants and helpers above, for the test modules."""
    return types.SimpleNamespace(CARD_FIELDS=CARD_FIELDS, NON_CARD_FIELDS=NON_CARD_FIELDS,
                                 NEW_FIELDS=NEW_FIELDS, entry=entry)
