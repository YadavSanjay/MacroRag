"""LLM synthesis of the structured shock vector from retrieved evidence.

Runs one grounded, JSON-constrained generation over the same chunks + numeric
series the prose answer used (the spec explicitly does NOT want NLP-parsing of
the prose). Output is validated against the controlled field vocabulary; the
citation GATE (gate.py) is applied separately by the pipeline.
"""

from __future__ import annotations

import re

from llm.base import LLMClient
from llm.generate import format_context, format_data
from llm.registry import get_registry
from llm.usage import prompt_context
from utils.logging import get_logger
from utils.schema import RetrievedChunk, TimeseriesHit

from .diagnostics import SynthesisDiagnostics
from .schema import ShockField
from .vocab import SHOCK_FIELDS, field_reference, is_valid_field, is_valid_unit

log = get_logger(__name__)


def _constraints_block(fields: list[str] | None, horizon_days: int | None,
                       directions: dict[str, str] | None = None) -> str:
    """Render the optional structured controls into the prompt's CONSTRAINTS
    section. Empty controls render as ``(none)`` so behaviour is unchanged."""
    lines: list[str] = []
    if horizon_days:
        lines.append(f"- Scenario horizon: {horizon_days} day(s). Every move is the "
                     "cumulative change over this horizon.")
    if fields:
        lines.append("- Emit ONLY these fields (still omit any you cannot ground in "
                     f"the evidence): {', '.join(fields)}.")
    if (line := direction_line(directions)) is not None:
        lines.append(line)
    return "\n".join(lines) if lines else "(none)"


def direction_line(directions: dict[str, str] | None) -> str | None:
    """The CONSTRAINTS line stating the caller's expected direction per field.

    Args:
        directions: Field name -> "up" or "down"; unknown names and other values
            are skipped.

    Returns:
        The line (spec synth-direction-prompt), or None when no entry is left.
    """
    if not directions:
        return None
    wanted = {str(k).upper(): v for k, v in directions.items()}
    entries = [f"{name} {d} ({'+' if d == 'up' else '-'}, positive means "
               f"{spec.positive_means})"
               for name, spec in SHOCK_FIELDS.items()
               if (d := wanted.get(name)) in ("up", "down")]
    if not entries:
        return None
    return ("- Expected directions (from the scenario; ground each field on evidence "
            "that moved this way): " + "; ".join(entries) + ".")


def synthesize_shock_vector(llm: LLMClient, query: str,
                            chunks: list[RetrievedChunk],
                            timeseries: list[TimeseriesHit] | None = None,
                            *, model: str | None = None,
                            fields: list[str] | None = None,
                            horizon_days: int | None = None,
                            diag: SynthesisDiagnostics | None = None,
                            purpose: str = "synthesize",
                            # field -> "up"/"down", stated in CONSTRAINTS (spec
                            # synth-direction-prompt)
                            directions: dict[str, str] | None = None,
                            ) -> dict[str, ShockField]:
    if not chunks:
        return {}
    allow = {f.upper() for f in fields} if fields else None
    rp = get_registry().resolve("synthesize", model=model)
    with prompt_context(rp.variant_id, rp.content_hash):
        parsed = llm.complete_json(system=rp.system, model=model, purpose=purpose,
            schema=rp.schema,
            user=rp.render(
                question=query, context=format_context(chunks),
                data=format_data(timeseries or []), fields=field_reference(),
                constraints=_constraints_block(fields, horizon_days, directions)))

    raw = parsed.get("shock_vector")
    if not isinstance(raw, dict):
        # tolerate a bare {FIELD: {...}} object without the wrapper
        raw = parsed if parsed and all(isinstance(v, dict) for v in parsed.values()) else {}

    if diag is not None:
        diag.emitted_fields = [str(k).upper() for k in raw.keys()]

    out: dict[str, ShockField] = {}
    for name, spec in raw.items():
        name = str(name).upper()
        if not isinstance(spec, dict):
            _note(diag, name, "malformed_spec")
            continue
        if not is_valid_field(name):
            _note(diag, name, "unknown_field")
            continue
        if allow is not None and name not in allow:   # caller restricted the field set
            _note(diag, name, "not_in_allowset")
            continue
        unit = str(spec.get("unit", "")).lower()
        if not is_valid_unit(name, unit):
            log.info("synthesis: dropping %s — invalid unit %r", name, unit)
            _note(diag, name, "invalid_unit", unit=unit)
            continue
        try:
            value = float(spec.get("value"))   # type: ignore[arg-type]  # None caught by except
        except (TypeError, ValueError):
            _note(diag, name, "non_numeric", value=spec.get("value"))
            continue
        # A zero magnitude is a positive claim of "no move" — almost always the
        # model padding a factor it couldn't anchor. Drop it: an ABSENT field
        # already means "no shock applied", so omitting is both truthful and
        # operationally identical, without the false "quantified as unchanged".
        if value == 0:
            _note(diag, name, "zero_magnitude")
            continue
        chunk_ids = [str(c) for c in (spec.get("chunk_ids") or []) if c]
        mode = str(spec.get("grounding_mode", "direct")).lower()
        if mode not in ("direct", "analog"):
            mode = "direct"
        out[name] = ShockField(name=name, value=value, unit=unit,
                               chunk_ids=chunk_ids,
                               rationale=str(spec.get("rationale", "")),
                               grounding_mode=mode,
                               source_horizon_days=_source_horizon(
                                   spec.get("source_horizon_days")))
    return out


def _source_horizon(raw: object) -> int | None:
    """The model's declared evidence horizon as a positive int, else None.

    Accepts an int, a string of one, or a whole-valued float; anything else
    (including bools and non-positive values) means "not declared".
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        n = raw
    elif isinstance(raw, float) and raw.is_integer():
        n = int(raw)
    elif isinstance(raw, str) and re.fullmatch(r"\s*[+-]?\d+\s*", raw):
        n = int(raw)
    else:
        return None
    return n if n > 0 else None


def _note(diag: SynthesisDiagnostics | None, name: str, reason: str, **extra) -> None:
    if diag is not None:
        diag.drop(name, "synthesize", reason, **extra)
