"""Scenario-synthesis: structured, cited shock vectors for the AITrade stress
engine (MacroRAG_synthesis_endpoint_spec.md)."""

from __future__ import annotations

from .diagnostics import SynthesisDiagnostics
from .gate import gate_shock_vector
from .schema import ShockField, shock_vector_to_dict
from .synthesizer import synthesize_shock_vector
from .validate import (
                       COHERENCE_RULES,
                       HARD_VALIDATION_CODES,
                       MAGNITUDE_BOUNDS,
                       check_analog_quality,
                       check_card_values,
                       check_direction,
                       check_episode_mix,
                       check_source_horizon,
                       reconcile_grounding_modes,
                       validate_shock_vector,
)
from .vocab import SHOCK_FIELDS, field_reference, is_valid_field, is_valid_unit, schema_dict

__all__ = [
    "ShockField", "shock_vector_to_dict", "synthesize_shock_vector",
    "gate_shock_vector", "SynthesisDiagnostics", "SHOCK_FIELDS",
    "field_reference", "schema_dict", "is_valid_field", "is_valid_unit",
    "validate_shock_vector", "reconcile_grounding_modes", "COHERENCE_RULES",
    "MAGNITUDE_BOUNDS", "HARD_VALIDATION_CODES", "check_analog_quality",
    "check_source_horizon", "check_direction", "check_episode_mix", "check_card_values",
]
