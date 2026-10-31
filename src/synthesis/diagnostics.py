"""Per-synthesize diagnostics: a structured record of WHY each shock field did or
did not ground. Makes the manual root-cause analysis (LLM emitted nothing vs the
citation gate dropped a field vs the upstream query refused) automatic and
inspectable via the query trace + the synthesis API response.

Populated cooperatively: the pipeline sets the skip/chunk fields, the synthesizer
records the LLM-emitted set and its validation drops, and the gate records its
citation drops. All optional — passing no ``diag`` leaves behaviour unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# stage -> reasons it can drop a field (documented for consumers)
SYNTHESIZE_REASONS = ("malformed_spec", "unknown_field", "not_in_allowset",
                      "invalid_unit", "non_numeric", "zero_magnitude")
GATE_REASONS = ("no_dated_citation",)
# Shock fields a market-episode card can carry: exactly the fields named in the card
# producer's map, ingest.usstats.episodes.CANONICAL (spec episode-card-fields pins the
# two together). MOVE and the rest have no daily series, so they can only come from prose.
EPISODE_CARD_FIELDS = frozenset({"VIX", "UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y",
                                 "IG_OAS", "HY_OAS", "USDTWD", "USDJPY",
                                 "REAL_YIELD_10Y", "TERM_PREMIUM_10Y"})


def missing_fields(requested: list[str] | None, kept: list[str] | dict,
                   dropped: list[dict]) -> list[dict]:
    """Why each requested shock field is absent from the vector (spec synth-vector-report).

    Args:
        requested: The caller's requested field names, any case.
        kept: The kept field names (or the kept mapping).
        dropped: The diagnostics drop records.

    Returns:
        One entry per requested, not-kept field: field, reason, codes, card_field.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for name in (str(r).upper() for r in requested or []):
        if name in seen or name in kept:
            continue
        seen.add(name)
        records = [d for d in dropped if d.get("field") == name]
        codes: list[str] = []
        for d in records:
            for code in (d["codes"] if "codes" in d else [d.get("reason")]):
                if code not in codes:
                    codes.append(code)
        out.append({"field": name, "reason": "dropped" if records else "not_emitted",
                    "codes": codes, "card_field": name in EPISODE_CARD_FIELDS})
    return out


@dataclass
class SynthesisDiagnostics:
    skipped: str | None = None            # None | 'upstream_refused' | 'no_chunks'
    n_chunks: int = 0
    n_dated_chunks: int = 0
    emitted_fields: list[str] = field(default_factory=list)   # LLM-emitted (pre-validation)
    kept_fields: list[str] = field(default_factory=list)      # survived synth + gate
    analog_fields: list[str] = field(default_factory=list)    # kept via analog/proxy grounding
    dropped: list[dict] = field(default_factory=list)         # {field, stage, reason, ...}
    validation: list[dict] = field(default_factory=list)      # numeric/coherence warnings
    missing_fields: list[dict] = field(default_factory=list)  # requested, not kept, and why

    def drop(self, name: str, stage: str, reason: str, **extra) -> None:
        self.dropped.append({"field": name, "stage": stage, "reason": reason, **extra})

    def warn(self, code: str, **extra) -> None:
        """Record a numeric-validation or coherence warning (surface-only unless a
        field was also dropped). ``code`` is the validation code; ``extra`` carries
        the offending field(s) + context (value, cap, span, message)."""
        self.validation.append({"code": code, **extra})

    @property
    def outcome(self) -> str:
        """One-word verdict for the whole synthesize call."""
        if self.skipped:
            return self.skipped              # 'upstream_refused' | 'no_chunks'
        if not self.emitted_fields:
            return "llm_empty"               # model returned {} — nothing to gate
        if not self.kept_fields:
            return "all_dropped"             # everything failed validation/gate
        if self.dropped:
            return "partial"                 # some kept, some dropped
        return "grounded"

    @property
    def coverage_gap(self) -> bool:
        """True when the scenario produced no grounded shock fields — the signal a
        consumer branches on to fall back to the prose + nearest evidence chunks
        instead of an unexplained empty vector."""
        return not self.kept_fields

    def summary(self) -> str:
        return (f"synthesis[{self.outcome}] kept={len(self.kept_fields)} "
                f"emitted={len(self.emitted_fields)} dropped={len(self.dropped)} "
                f"warnings={len(self.validation)} "
                f"chunks={self.n_chunks}({self.n_dated_chunks} dated)")

    def as_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "coverage_gap": self.coverage_gap,
            "skipped": self.skipped,
            "n_chunks": self.n_chunks,
            "n_dated_chunks": self.n_dated_chunks,
            "emitted_fields": self.emitted_fields,
            "kept_fields": self.kept_fields,
            "analog_fields": self.analog_fields,
            "dropped": self.dropped,
            "validation": self.validation,
            "missing_fields": self.missing_fields,
        }
