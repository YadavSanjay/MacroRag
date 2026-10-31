"""Shock-accuracy evaluation runner.

Drives the synthesizer over a set of scenario *cases*, each carrying a reference
shock path, and scores the emitted vector against it (shock_metrics.py). Two ways
to get a reference:

1. **Authored** — a YAML case lists ``reference: {FIELD: {value, unit, tol}}``.
2. **Store-derived** — ``derive_reference_from_store`` computes a reference from
   the DFAST/EBA scenario paths *already ingested* in the TimeseriesStore: the
   baseline→adverse delta of a level series (e.g. ``treasury_10y`` -> ``UST_10Y``)
   or of a spread (``bbb_corporate_yield`` − ``treasury_10y`` -> ``IG_OAS``). This
   is the "backtest against the ingested official paths" path.

The runner takes an injected ``synth_fn`` so it runs under test with a fake and in
prod with the real pipeline.synthesize.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import yaml

from utils.logging import get_logger
from utils.schema import MetaFilter
from vectorstores.timeseries_store import TimeseriesStore

from . import shock_metrics

log = get_logger(__name__)

# synth_fn(question, *, horizon_days, base_filter) -> {FIELD: ShockField|dict}
SynthFn = Callable[..., dict]


def load_shock_cases(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data.get("cases", [])


# ── store-derived reference (backtest against ingested DFAST/EBA paths) ────────

# Default DFAST-style mapping: shock field -> how to derive its delta from the
# stored scenario paths. kind=level -> delta of one variable; kind=spread ->
# delta of the (minuend - subtrahend) spread. ``scale`` converts the series unit
# to the field unit (rate/yield levels are in percent -> ×100 for a ``bp`` field).
DEFAULT_STORE_MAPPING: dict[str, dict] = {
    "UST_5Y":  {"kind": "level", "variable": "treasury_5y", "unit": "bp", "scale": 100.0},
    "UST_10Y": {"kind": "level", "variable": "treasury_10y", "unit": "bp", "scale": 100.0},
    "VIX":     {"kind": "level", "variable": "vix", "unit": "pts", "scale": 1.0},
    "IG_OAS":  {"kind": "spread", "minuend": "bbb_corporate_yield",
                "subtrahend": "treasury_10y", "unit": "bp", "scale": 100.0},
    # `relative` converts an index-LEVEL delta into a percent move, since the
    # equity field is quoted in pct while the stored series is an index level.
    "EQUITY_US": {"kind": "level", "variable": "dow_jones", "unit": "pct",
                  "scale": 1.0, "relative": True},
}


def _relativise(delta: float, base: list[float], spec: dict) -> float | None:
    """Percent-ise a level delta against the baseline mean when ``relative``."""
    if not spec.get("relative"):
        return delta
    ref = (sum(base) / len(base)) if base else 0.0
    if not ref:
        return None                      # no meaningful base -> no reference
    return delta / ref * 100.0


def _ordered_vals(store: TimeseriesStore, *, source_like: str, scenario: str,
                  variable: str) -> list[float]:
    """Numeric path for (scenario, variable) in period order (store insertion order
    is chronological)."""
    rows = store.query(source_like=source_like, scenario=scenario,
                       variable=variable, limit=2000)
    return [float(r["value"]) for r in rows if r.get("value") is not None]


def _reduce_delta(baseline: list[float], adverse: list[float], *, reduce: str,
                  peak_percentile: float = 90.0) -> float | None:
    """Collapse a baseline vs adverse path pair to a single shock magnitude.

    ``peak`` (default) = the worst-case deviation from the baseline mean, taken as
    the ``peak_percentile``-th percentile of |deviation| carrying the stress
    direction — a robust peak that captures a mean-reverting spike (VIX to 72 then
    back to 28) WITHOUT chasing a lone outlier print (percentile=100 recovers the
    raw absolute max). ``last`` = end-of-horizon delta; ``mean`` = mean-to-mean."""
    if not baseline or not adverse:
        return None
    base_ref = sum(baseline) / len(baseline)
    if reduce == "last":
        return adverse[-1] - base_ref
    if reduce == "mean":
        return sum(adverse) / len(adverse) - base_ref
    # peak: robust worst-case. Rank |deviation|, take the percentile magnitude,
    # and restore the sign of the single most extreme point (the stress direction).
    devs = [a - base_ref for a in adverse]
    abs_sorted = sorted(abs(d) for d in devs)
    k = max(1, math.ceil(peak_percentile / 100.0 * len(abs_sorted)))
    magnitude = abs_sorted[min(k, len(abs_sorted)) - 1]
    return math.copysign(magnitude, max(devs, key=abs))


def derive_reference_from_store(store: TimeseriesStore, *, source_like: str,
                                baseline_scenario: str, adverse_scenario: str,
                                mapping: dict[str, dict] | None = None,
                                reduce: str = "peak", peak_percentile: float = 90.0,
                                default_tol_frac: float = 0.5) -> dict[str, dict]:
    """Build a reference {FIELD: {value, unit, tol}} from the peak baseline→adverse
    stress of the ingested scenario paths. For a spread field the (minuend −
    subtrahend) spread is formed per period first, then reduced. ``tol`` defaults
    to ``default_tol_frac`` of |delta| (a proportional band)."""
    mapping = mapping or DEFAULT_STORE_MAPPING

    def path(scenario, variable):
        return _ordered_vals(store, source_like=source_like,
                             scenario=scenario, variable=variable)

    ref: dict[str, dict] = {}
    for field, spec in mapping.items():
        scale = float(spec.get("scale", 1.0))
        if spec["kind"] == "level":
            base, adv = path(baseline_scenario, spec["variable"]), \
                path(adverse_scenario, spec["variable"])
        elif spec["kind"] == "spread":
            m, s = spec["minuend"], spec["subtrahend"]
            bm, bs = path(baseline_scenario, m), path(baseline_scenario, s)
            am, as_ = path(adverse_scenario, m), path(adverse_scenario, s)
            base = [x - y for x, y in zip(bm, bs)]      # per-period spread
            adv = [x - y for x, y in zip(am, as_)]
        else:
            log.warning("unknown store-mapping kind %r for %s", spec["kind"], field)
            continue
        delta = _reduce_delta(base, adv, reduce=reduce,
                              peak_percentile=peak_percentile)
        if delta is None:
            continue
        delta = _relativise(delta, base, spec)
        if delta is None:
            continue
        delta *= scale
        ref[field] = {"value": round(delta, 4), "unit": spec["unit"],
                      "tol": round(abs(delta) * default_tol_frac, 4)}
    return ref


# ── runner ────────────────────────────────────────────────────────────────────

def _base_filter(item: dict) -> MetaFilter | None:
    f = item.get("filter")
    return MetaFilter(**f) if f else None


def _case_reference(item: dict, store: TimeseriesStore | None) -> dict:
    """The reference for a case: its authored ``reference`` block, overlaid with a
    store-derived one when the case carries a ``derive`` block and a store is
    available (store-derived fields win, so an authored value is a fallback for
    when the numeric path isn't indexed)."""
    reference = dict(item.get("reference") or {})
    spec = item.get("derive")
    if spec and store is not None:
        try:
            derived = derive_reference_from_store(
                store, source_like=spec["source_like"],
                baseline_scenario=spec["baseline_scenario"],
                adverse_scenario=spec["adverse_scenario"],
                mapping=spec.get("mapping"),
                reduce=spec.get("reduce", "peak"),
                peak_percentile=spec.get("peak_percentile", 90.0))
            reference.update(derived)
        except Exception:                       # a bad derive spec must not kill the run
            log.warning("shock-eval: derive failed for case %r",
                        item.get("id"), exc_info=True)
    return reference


def evaluate_shock_accuracy(synth_fn: SynthFn, cases: list[dict[str, Any]], *,
                            store: TimeseriesStore | None = None,
                            default_tol: float | None = None) -> dict[str, Any]:
    """Run each case through ``synth_fn`` and score the emitted vector against its
    reference. A case needs ``question`` + a reference (authored ``reference`` and/
    or a store-derived ``derive`` block); ``horizon_days`` and ``filter`` are
    optional. Returns {summary, rows}."""
    rows, summaries = [], []
    for item in cases:
        reference = _case_reference(item, store)
        if not reference:
            log.info("shock-eval: case %r has no reference; skipping",
                     item.get("id") or item.get("question"))
            continue
        emitted = synth_fn(item["question"],
                           horizon_days=item.get("horizon_days"),
                           base_filter=_base_filter(item)) or {}
        scored = shock_metrics.score_shock_vector(
            emitted, reference, default_tol=item.get("default_tol", default_tol))
        summaries.append(scored["summary"])
        rows.append({"id": item.get("id") or item["question"][:48],
                     "question": item["question"], **scored})
    return {"summary": shock_metrics.aggregate_shock(summaries), "rows": rows}
