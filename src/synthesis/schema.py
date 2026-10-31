"""Shock-vector data contract returned to the AITrade stress engine."""

from __future__ import annotations

from dataclasses import dataclass, field

GROUNDING_MODES = ("direct", "analog")


@dataclass
class ShockField:
    """One driver in the shock vector: a signed magnitude in `unit`, cited.

    ``grounding_mode`` records how the magnitude was anchored: ``direct`` when the
    evidence quantifies this factor for the scenario itself, ``analog`` when it is
    a proxy anchored to the closest historical analog episode or official adverse
    scenario in the cited chunk (still dated + cited, never invented). Lets the
    consumer down-weight proxies without losing them."""
    name: str
    value: float
    unit: str
    chunk_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    grounding_mode: str = "direct"
    # Numeric-validation codes attached by the validation/coherence pass
    # (synthesis/validate.py): e.g. ``implausible_magnitude``,
    # ``exceeds_observed_range``, or a coherence-rule code. Empty when the field
    # passed all checks. Surfaced to the consumer so a flagged-but-kept field can
    # be down-weighted rather than silently trusted.
    validation: list[str] = field(default_factory=list)
    # Horizon (days) of the move the evidence describes, as the model declared it.
    # None = not declared. Checked against the requested horizon by
    # validate.check_source_horizon.
    source_horizon_days: int | None = None

    def to_dict(self) -> dict:
        out: dict = {"value": self.value, "unit": self.unit,
                     "chunk_ids": list(self.chunk_ids),
                     "grounding_mode": self.grounding_mode}
        if self.rationale:
            out["rationale"] = self.rationale
        if self.validation:
            out["validation"] = list(self.validation)
        if self.source_horizon_days is not None:
            out["source_horizon_days"] = self.source_horizon_days
        return out


def shock_vector_to_dict(fields: dict[str, ShockField]) -> dict:
    return {name: f.to_dict() for name, f in fields.items()}
