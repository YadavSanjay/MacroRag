"""Data contract for factor discovery — the ranked, cited macro risk factors
returned to a consumer exploring what drives a portfolio/theme."""

from __future__ import annotations

from dataclasses import dataclass, field

# Allowed relevance grades (ordered most → least for deterministic sorting).
RELEVANCE_ORDER = ("high", "medium", "low")


@dataclass
class RelevantFactor:
    """One macro risk factor judged relevant to the portfolio, grounded in cited
    evidence. ``factor_tag`` is drawn from the controlled factor vocabulary."""
    factor_tag: str
    relevance: str = "medium"
    entity_tags: list[str] = field(default_factory=list)
    rationale: str = ""
    chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict = {
            "factor_tag": self.factor_tag,
            "relevance": self.relevance,
            "entity_tags": list(self.entity_tags),
            "chunk_ids": list(self.chunk_ids),
        }
        if self.rationale:
            out["rationale"] = self.rationale
        return out


def factors_to_dict(factors: list[RelevantFactor]) -> list[dict]:
    return [f.to_dict() for f in factors]
