"""Factor discovery: ranked, cited macro risk factors relevant to a portfolio
or theme. Same controlled-vocabulary + citation-gate discipline as scenario
synthesis, but the output is *which factors matter* (the upstream exploration
step) rather than *how big the shocks are*."""

from __future__ import annotations

from .discoverer import discover_factors
from .gate import gate_factors
from .schema import RelevantFactor, factors_to_dict
from .vocab import factor_reference, valid_entity_tags, valid_factor_tags

__all__ = [
    "discover_factors", "gate_factors", "RelevantFactor", "factors_to_dict",
    "factor_reference", "valid_factor_tags", "valid_entity_tags",
]
