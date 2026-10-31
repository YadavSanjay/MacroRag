"""Pure scoring for shock-vector accuracy — compare an emitted shock vector to a
reference path (a DFAST/EBA official scenario, or a hand-authored expectation).

Retrieval metrics score *whether the right chunks came back*; these score
*whether the synthesized numbers are right*. Per reference field we record:
coverage (did the vector emit it), sign agreement, absolute error in the field's
unit, and whether it landed inside a tolerance band. Provider-free and testable —
the runner (shock_eval.py) supplies emitted vs reference, this does the math.
"""

from __future__ import annotations

from typing import Any


def _val_unit(x: Any) -> tuple[float | None, str | None]:
    """Normalise an emitted field (a ShockField or a plain {value,unit} dict) to
    a (value, unit) pair. Returns (None, None) when absent/malformed."""
    if x is None:
        return None, None
    value = getattr(x, "value", None)
    unit = getattr(x, "unit", None)
    if value is None and isinstance(x, dict):
        value, unit = x.get("value"), x.get("unit")
    try:
        return (float(value) if value is not None else None), unit
    except (TypeError, ValueError):
        return None, unit


def _sign(x: float, eps: float = 1e-9) -> int:
    if x > eps:
        return 1
    if x < -eps:
        return -1
    return 0


def score_field(emitted: Any, ref: dict, *, default_tol: float | None = None) -> dict:
    """Score one reference field. ``ref`` = {value, unit, tol?}. ``emitted`` is the
    matching ShockField/dict from the vector, or None if it wasn't emitted."""
    r_val = float(ref["value"])
    r_unit = ref.get("unit")
    tol = ref.get("tol", default_tol)
    e_val, e_unit = _val_unit(emitted)

    out: dict[str, Any] = {
        "reference": r_val, "unit": r_unit, "emitted": e_val,
        "present": e_val is not None,
        "unit_match": (e_unit == r_unit) if e_val is not None else None,
        "sign_match": None, "abs_error": None, "within_tol": False,
        "magnitude_ratio": None, "tol": tol,
    }
    if e_val is None:
        return out                                   # missing field: not within tol
    out["sign_match"] = _sign(e_val) == _sign(r_val)
    out["abs_error"] = abs(e_val - r_val)
    if r_val != 0:
        out["magnitude_ratio"] = abs(e_val) / abs(r_val)
    # A field is "within tolerance" only if the unit matches AND the error is
    # inside the band (a unit mismatch means the numbers aren't comparable).
    if out["unit_match"] and tol is not None:
        out["within_tol"] = out["abs_error"] <= float(tol)
    return out


def score_shock_vector(emitted: dict, reference: dict, *,
                       default_tol: float | None = None) -> dict:
    """Score a full emitted vector against a reference (field -> {value,unit,tol}).
    Returns per-field detail + a case-level summary. ``emitted`` keys/values are
    ShockField objects or {value,unit} dicts; extra emitted fields not in the
    reference are reported but don't affect the accuracy metrics."""
    fields: dict[str, dict] = {}
    for name, ref in reference.items():
        fields[name] = score_field(emitted.get(name), ref, default_tol=default_tol)

    n_ref = len(reference)
    present = [f for f in fields.values() if f["present"]]
    n_present = len(present)
    sign_ok = sum(1 for f in present if f["sign_match"])
    within = sum(1 for f in fields.values() if f["within_tol"])
    # normalised error (abs_error / tol) over emitted fields that have a tol
    norm_errors = [f["abs_error"] / float(f["tol"]) for f in present
                   if f["tol"] not in (None, 0) and f["unit_match"]]
    ratios = [f["magnitude_ratio"] for f in present if f["magnitude_ratio"] is not None]

    summary = {
        "coverage": n_present / n_ref if n_ref else 0.0,          # emitted / reference
        "sign_accuracy": sign_ok / n_present if n_present else 0.0,  # of emitted
        "tolerance_rate": within / n_ref if n_ref else 0.0,       # of reference (miss=fail)
        "mean_norm_error": (sum(norm_errors) / len(norm_errors)) if norm_errors else None,
        "mean_magnitude_ratio": (sum(ratios) / len(ratios)) if ratios else None,
        "n_reference": n_ref, "n_emitted": n_present,
        "extra_fields": sorted(set(emitted) - set(reference)),
    }
    return {"summary": summary, "fields": fields}


_MEAN_KEYS = ("coverage", "sign_accuracy", "tolerance_rate",
              "mean_norm_error", "mean_magnitude_ratio")


def aggregate_shock(case_summaries: list[dict]) -> dict:
    """Mean each accuracy metric across cases (skipping None contributions, so a
    case that emitted nothing doesn't poison the magnitude means)."""
    if not case_summaries:
        return {}
    agg: dict[str, Any] = {"n_cases": len(case_summaries)}
    for key in _MEAN_KEYS:
        vals = [s[key] for s in case_summaries if s.get(key) is not None]
        agg[key] = (sum(vals) / len(vals)) if vals else None
    return agg
