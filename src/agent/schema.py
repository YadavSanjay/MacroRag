"""Typed plan-and-execute schema (ARag review §2.4.2). Instead of free-text
subquestions, the planner emits a typed plan whose numeric nodes hit the
structured time-series store first; arithmetic is a deterministic compute node;
narrative nodes fall back to RAG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from utils.schema import RetrievedChunk, TimeseriesHit


@dataclass
class DataStep:
    """A structured lookup against the time-series store, reduced to a scalar."""
    id: str
    source_like: str                      # e.g. "DFAST", "SEP", "DFAST_2026"
    variable: str | None = None        # e.g. unemployment_rate, federal_funds_rate
    scenario: str | None = None        # e.g. severely_adverse, baseline
    statistic: str | None = None       # e.g. median (SEP)
    period: str | None = None          # exact period for aggregate=value
    aggregate: str = "value"              # value|max|min|first|last|mean|count


@dataclass
class RetrieveStep:
    id: str
    query: str                            # narrative RAG sub-query


@dataclass
class ComputeStep:
    id: str
    expression: str                       # references prior step ids (calculator)
    label: str = ""


@dataclass
class Plan:
    quantitative: bool = False
    data: list[DataStep] = field(default_factory=list)
    retrieve: list[RetrieveStep] = field(default_factory=list)
    compute: list[ComputeStep] = field(default_factory=list)


@dataclass
class PlanResult:
    plan: Plan
    values: dict[str, float]              # step id -> scalar
    data_hits: list[TimeseriesHit]
    computations: list[dict[str, Any]]    # {id,label,expression,value|error}
    chunks: list[RetrievedChunk]

    def computed_hit(self) -> TimeseriesHit | None:
        """Computed values as a DATA section the generator/verifier can cite."""
        rows = [c for c in self.computations if "value" in c]
        if not rows:
            return None
        return TimeseriesHit(source="COMPUTED", publisher="calculator",
                             rows=rows, note="deterministic calculations")
