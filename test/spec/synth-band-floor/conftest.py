"""Fixtures for the synth-band-floor spec tests.

Hand-built ``ShockField``s and the spec's Definitions written out by hand: today's caps
(copied from the spec), the floors, and the horizon factor. No network, no ``data/``, no
clock.
"""

from __future__ import annotations

import math
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

CODE = "implausible_magnitude"
# spec C1: today's caps for the fields the tests use (field -> (unit, cap)).
CAPS = {"VIX": ("pts", 70.0), "UST_2Y": ("bp", 400.0), "UST_5Y": ("bp", 400.0),
        "UST_10Y": ("bp", 350.0), "UST_30Y": ("bp", 350.0), "UST": ("bp", 350.0),
        "IG_OAS": ("bp", 500.0), "HY_OAS": ("bp", 2000.0), "USDTWD": ("pct", 50.0)}
# spec Definitions: the floors.
FLOORS = {"VIX": 1.0, "UST_2Y": 0.5, "UST_5Y": 0.5, "UST_10Y": 0.5, "UST_30Y": 0.5,
          "UST": 0.5}


def factor(h: int | None, ref: int = 90) -> float:
    """spec Definitions: today's horizon factor."""
    if h is None or h <= 0 or ref <= 0:
        return 1.0
    return min(math.sqrt(h / ref), 3.0)


def limit(field: str, h: int | None, ref: int = 90) -> float:
    """spec Definitions: cap x max(factor, floor)."""
    return CAPS[field][1] * max(factor(h, ref), FLOORS.get(field, 0.0))


def sf(name: str, value: float, unit: str):
    from synthesis.schema import ShockField
    return ShockField(name, value, unit, chunk_ids=["c::000"], rationale="",
                      grounding_mode="analog")


def flagged(name: str, value: float, unit: str, h: int | None) -> bool:
    """Whether validate_shock_vector flags this one field implausible at horizon h."""
    from synthesis.validate import validate_shock_vector
    fields = {name: sf(name, value, unit)}
    validate_shock_vector(fields, horizon_days=h, drop_invalid=False)
    return CODE in fields[name].validation


@pytest.fixture(scope="session")
def sbf():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(CODE=CODE, CAPS=CAPS, FLOORS=FLOORS, factor=factor,
                                 limit=limit, sf=sf, flagged=flagged)
