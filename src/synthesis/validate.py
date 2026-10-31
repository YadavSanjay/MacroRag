"""Numeric-validation / coherence pass for the synthesized shock vector.

The synthesizer's magnitudes are LLM-emitted (read from cited evidence). The
citation gate proves each field is *grounded in a dated chunk* but says nothing
about whether the *number itself* is plausible or whether the fields hang
together as one scenario. This pass adds that layer, in three cheap, dependency-
free checks run on the already-gated fields:

1. **Plausibility band** — each magnitude is checked against a per-field soft cap
   (a "this is almost certainly a hallucination" ceiling), scaled up with the
   scenario horizon (vol grows ~sqrt-time, capped). A breach flags
   ``implausible_magnitude``.
2. **Series anchor** — when the answer hydrated a stored numeric series that maps
   to a shock field (e.g. ``treasury_10y`` -> ``UST_10Y``), the emitted move is
   compared against the observed span of that series; a move that dwarfs it flags
   ``exceeds_observed_range``. This is the "tie magnitudes back to the stored
   series" check — it only fires when matching data is actually present.
3. **Cross-field coherence** — a small rule set of expected co-movements
   (IG/HY OAS same-sign; equity-vol/HY-credit same-sign; VIX/MOVE co-move) and
   anti-movements (EQUITY_US is a *level*, so it moves opposite to VIX and to HY
   spreads in a risk event). A violation flags both participating fields.

Default behaviour is **annotate, don't drop** (the design principle: surface
conflicts rather than silently choosing). ``drop_invalid=True`` drops only fields
carrying a *hard* magnitude failure — coherence and series-anchor flags are
always surface-only, since they are pairwise/soft judgements.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TypeGuard

from utils.logging import get_logger
from utils.schema import RetrievedChunk, TimeseriesHit

from .diagnostics import SynthesisDiagnostics
from .schema import ShockField
from .vocab import DOC_TYPE_MIN_HORIZON_DAYS, FX_CODES, FX_PROXY_ALLOWLIST, SHOCK_FIELDS

log = get_logger(__name__)


# ── plausibility bands ────────────────────────────────────────────────────────
# Per field -> {unit -> soft cap on |value|} at the REFERENCE horizon. Generous
# by design: these catch gross LLM errors (sign-flipped typos, unit confusion,
# fat-fingered zeros), not tight calibration. Tunable, but every value here is a
# defensible ceiling on a single-scenario macro move at ~1 quarter.
MAGNITUDE_BOUNDS: dict[str, dict[str, float]] = {
    "UST_2Y":  {"bp": 400.0}, "UST_5Y": {"bp": 400.0},
    "UST_10Y": {"bp": 350.0}, "UST_30Y": {"bp": 350.0}, "UST": {"bp": 350.0},
    "IG_OAS":  {"bp": 500.0}, "HY_OAS": {"bp": 2000.0},
    "MOVE":    {"rel": 3.0, "pts": 200.0},
    "MOVE_1Y": {"rel": 3.0, "pts": 200.0}, "MOVE_5Y": {"rel": 3.0, "pts": 200.0},
    "MOVE_10Y": {"rel": 3.0, "pts": 200.0}, "MOVE_30Y": {"rel": 3.0, "pts": 200.0},
    "VIX": {"pts": 70.0},
    # Deepest realized drawdown in the ingested history is ~-43% (GFC, measured
    # off the pre-crisis quarterly mean); 60 leaves headroom for a worse tail
    # without admitting a fat-fingered zero.
    "EQUITY_US": {"pct": 60.0},
    "USDTWD":  {"pct": 50.0}, "USDJPY": {"pct": 50.0},
    "XCCY_BASIS": {"bp": 200.0},
    "REAL_YIELD_10Y": {"bp": 350.0}, "TERM_PREMIUM_10Y": {"bp": 200.0},
}

# The rates-vol term structure (aggregate MOVE + per-expiry nodes). A vol shock is
# normally same-direction across expiries, so a materially-moving node that fights
# the rest of the curve is worth surfacing.
RATES_VOL_FIELDS: tuple[str, ...] = ("MOVE", "MOVE_1Y", "MOVE_5Y", "MOVE_10Y", "MOVE_30Y")

# Horizon scaling: |cap| grows with sqrt(horizon / reference), capped so a
# multi-year horizon can't license an absurd move.
_MAX_HORIZON_SCALE = 3.0

# Floor on the horizon factor (spec synth-band-floor). Vol and short-rate shocks jump
# rather than diffuse, so sqrt-scaling a quarterly cap down to 5-10 days rejected
# realized moves (tariff 2025: VIX +30.8 pts in 5 days; SVB: UST_2Y -112 bp in 5).
# A floored field's limit never drops below this share of its cap. Fields not
# listed keep pure sqrt scaling.
MAGNITUDE_SCALE_FLOOR: dict[str, float] = {
    "VIX": 1.0,
    "UST_2Y": 0.5, "UST_5Y": 0.5, "UST_10Y": 0.5, "UST_30Y": 0.5, "UST": 0.5,
    "REAL_YIELD_10Y": 0.5, "TERM_PREMIUM_10Y": 0.5,
}

# A field only participates in coherence checks once it has "materially" moved,
# so near-zero noise (a +1bp IG vs -1bp HY) is never called incoherent.
_MATERIAL_FLOOR: dict[str, float] = {"bp": 5.0, "pts": 1.0, "rel": 0.05, "pct": 0.5}

# Stored time-series variable (utils/pipelines hydration vocab) -> shock field it
# can anchor. Deliberately conservative: only mappings that are a true tenor/name
# match (or an accepted credit proxy) are listed, so the anchor never fires on a
# mismatched series.
SHOCK_FIELD_TO_SERIES: dict[str, str] = {
    "UST_5Y": "treasury_5y",
    "UST_10Y": "treasury_10y",
    "VIX": "vix",
    "IG_OAS": "bbb_corporate_yield",   # BBB level as the IG-credit proxy
    "HY_OAS": "bbb_corporate_yield",
}

# Stricter map for GROUNDING reconciliation: only fields whose stored series is a
# GENUINE direct measure of the factor, so a data-backed shock is legitimately
# "direct". Excludes HY_OAS — the store has no high-yield series, so a HY shock is
# always a proxy (the BBB/IG series above is only a soft magnitude anchor for it,
# not a direct measure), and must stay "analog".
RECONCILE_FIELD_TO_SERIES: dict[str, str] = {
    "UST_5Y": "treasury_5y",
    "UST_10Y": "treasury_10y",
    "VIX": "vix",
    "IG_OAS": "bbb_corporate_yield",
}
# The stored series carrying a rate/yield level is in PERCENT; a shock field in
# ``bp`` needs the observed span multiplied by 100 to compare like-for-like.
_PCT_TO_BP = 100.0
# A move may exceed the observed scenario span by up to this factor before it is
# flagged (scenario paths are one realisation, not the full plausible range).
_OBSERVED_RANGE_FACTOR = 3.0

# Validation codes that ``drop_invalid`` removes. Only unambiguous per-field
# failures (magnitude, unit scale, evidence horizon, a sign against the requested
# direction) — coherence/anchor codes and ``horizon_unknown`` are surface-only.
HARD_VALIDATION_CODES = frozenset({"implausible_magnitude", "unit_scale_mismatch",
                                   "horizon_mismatch", "direction_mismatch",
                                   "card_value_mismatch"})

# A market-episode card line (spec market-episodes): its largest move is the shock a
# field citing the card must carry (spec synth-card-value). Tolerance is half the
# card's last printed decimal per unit. A 1-day line reads "trading day" (spec
# episode-card-horizons).
_CARD_LINE = re.compile(
    r"^- (?P<label>.+?): [+-]?\d+(?:\.\d+)? \S+ after \d+ trading days? \([^)]*\); "
    r"largest move within the window (?P<peak>[+-]?\d+(?:\.\d+)?) \S+$", re.MULTILINE)
_CARD_TOLERANCE = {"bp": 0.05, "pts": 0.005, "pct": 0.005}


@dataclass(frozen=True)
class CoherenceRule:
    code: str
    fields: tuple[str, str]
    relation: str          # "same_sign" | "opposite_sign"
    hard: bool
    message: str


# Small, defensible co-movement rule set. `hard` rules are the ones a strict
# consumer would treat as a real inconsistency; `soft` ones are tendencies.
COHERENCE_RULES: tuple[CoherenceRule, ...] = (
    CoherenceRule("credit_indices_diverge", ("IG_OAS", "HY_OAS"), "same_sign", True,
                  "IG and HY OAS should widen or tighten together"),
    CoherenceRule("vol_credit_diverge", ("VIX", "HY_OAS"), "same_sign", True,
                  "equity vol and HY credit should share direction (risk-on/off)"),
    CoherenceRule("cross_vol_diverge", ("VIX", "MOVE"), "same_sign", False,
                  "equity vol and rates vol typically co-move"),
    # EQUITY_US is a level and VIX is a vol, so risk-off moves them in OPPOSITE
    # directions — equities down, vol up. Hard: a scenario claiming both rise
    # materially is describing two different worlds.
    CoherenceRule("equity_vol_diverge", ("EQUITY_US", "VIX"), "opposite_sign", True,
                  "equity level and equity vol move opposite ways in a risk event"),
    CoherenceRule("equity_credit_diverge", ("EQUITY_US", "HY_OAS"), "opposite_sign",
                  False, "equities usually fall as HY credit widens"),
)


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _horizon_scale(horizon_days: int | None, reference_days: int) -> float:
    if not horizon_days or horizon_days <= 0 or reference_days <= 0:
        return 1.0
    return min(math.sqrt(horizon_days / reference_days), _MAX_HORIZON_SCALE)


def _series_span(timeseries: list[TimeseriesHit] | None, variable: str) -> float | None:
    """Observed max-min of ``variable`` across all hydrated series rows, or None
    if the variable isn't present / has <2 numeric points."""
    if not timeseries:
        return None
    vals: list[float] = []
    for hit in timeseries:
        for row in hit.rows:
            if row.get("variable") != variable:
                continue
            v = row.get("value")
            try:
                f = float(v)   # type: ignore[arg-type]  # None caught by except
            except (TypeError, ValueError):
                continue
            if math.isfinite(f):
                vals.append(f)
    if len(vals) < 2:
        return None
    return max(vals) - min(vals)


def magnitude_limit(field: str, unit: str, *, horizon_days: int | None,
                    reference_days: int = 90) -> float | None:
    """The plausibility limit on ``|value|`` for a field and unit at a horizon.

    The field's quarterly cap times the horizon factor, which a per-field floor
    keeps from shrinking below a set share of the cap (spec synth-band-floor).

    Args:
        field: The shock field name.
        unit: The field's unit.
        horizon_days: The scenario horizon; None or <= 0 means the reference.
        reference_days: The horizon the caps are set for.

    Returns:
        The limit, or None when the field has no cap in that unit.
    """
    cap = MAGNITUDE_BOUNDS.get(field, {}).get(unit)
    if cap is None:
        return None
    scale = _horizon_scale(horizon_days, reference_days)
    return cap * max(scale, MAGNITUDE_SCALE_FLOOR.get(field, 0.0))


def _check_magnitude(f: ShockField, horizon_days: int | None, reference_days: int,
                     diag: SynthesisDiagnostics | None) -> None:
    limit = magnitude_limit(f.name, f.unit, horizon_days=horizon_days,
                            reference_days=reference_days)
    if limit is None or not math.isfinite(f.value):
        return
    if abs(f.value) > limit:
        f.validation.append("implausible_magnitude")
        if diag is not None:
            diag.warn("implausible_magnitude", field=f.name,
                      value=f.value, unit=f.unit, cap=round(limit, 3))


def _check_series_anchor(f: ShockField, timeseries: list[TimeseriesHit] | None,
                         diag: SynthesisDiagnostics | None) -> None:
    var = SHOCK_FIELD_TO_SERIES.get(f.name)
    if var is None:
        return
    span = _series_span(timeseries, var)
    if span is None or span <= 0:
        return
    observed = span * _PCT_TO_BP if f.unit == "bp" else span
    if abs(f.value) > observed * _OBSERVED_RANGE_FACTOR:
        f.validation.append("exceeds_observed_range")
        if diag is not None:
            diag.warn("exceeds_observed_range", field=f.name, value=f.value,
                      observed_span=round(observed, 3), variable=var)


def _material(f: ShockField) -> bool:
    floor = _MATERIAL_FLOOR.get(f.unit, 0.0)
    return math.isfinite(f.value) and abs(f.value) >= floor


def _check_coherence(fields: dict[str, ShockField],
                     diag: SynthesisDiagnostics | None) -> None:
    for rule in COHERENCE_RULES:
        a_name, b_name = rule.fields
        a, b = fields.get(a_name), fields.get(b_name)
        if a is None or b is None or not (_material(a) and _material(b)):
            continue
        product = _sign(a.value) * _sign(b.value)
        violated = ((rule.relation == "same_sign" and product < 0)
                    or (rule.relation == "opposite_sign" and product > 0))
        if not violated:
            continue
        a.validation.append(rule.code)
        b.validation.append(rule.code)
        if diag is not None:
            diag.warn(rule.code, fields=[a_name, b_name], hard=rule.hard,
                      message=rule.message)


def _check_rates_vol_term(fields: dict[str, ShockField],
                          diag: SynthesisDiagnostics | None) -> None:
    """Term-structure consistency for the rates-vol curve: if ≥2 rates-vol nodes
    have moved materially and they don't all share sign, flag the minority nodes
    (soft — a genuine twist is possible, but usually a synthesis slip)."""
    present = [fields[n] for n in RATES_VOL_FIELDS
               if n in fields and _material(fields[n])]
    if len(present) < 2:
        return
    signs = {_sign(f.value) for f in present}
    if len(signs) < 2:
        return                       # all same direction -> consistent
    majority = 1 if sum(_sign(f.value) for f in present) >= 0 else -1
    for f in present:
        if _sign(f.value) != majority:
            f.validation.append("rates_vol_term_inconsistent")
            if diag is not None:
                diag.warn("rates_vol_term_inconsistent", field=f.name,
                          value=f.value, hard=False,
                          message="rates-vol node fights the term-structure direction")


def _scenario_direction(by_var: dict[str, dict[str, list[float]]], variable: str
                        ) -> int:
    """Sign of the baseline→adverse move of ``variable`` in the hydrated data. The
    adverse scenario is the non-baseline one whose mean deviates most from baseline
    (the stress leg). Returns 0 when it can't be determined."""
    scen_means = {s: sum(xs) / len(xs)
                  for s, xs in by_var.get(variable, {}).items() if xs}
    base = scen_means.get("baseline")
    others = {s: m for s, m in scen_means.items() if s != "baseline"}
    if base is None or not others:
        return 0
    adverse = max(others, key=lambda s: abs(others[s] - base))
    return _sign(scen_means[adverse] - base)


def reconcile_grounding_modes(fields: dict[str, ShockField],
                             timeseries: list[TimeseriesHit] | None,
                             *, diag: SynthesisDiagnostics | None = None) -> None:
    """Upgrade a field's ``grounding_mode`` from ``analog`` to ``direct`` when its
    OWN scenario series was in the hydrated DATA and the emitted shock agrees with
    that path's stress direction. The scenario's own numeric path IS direct
    evidence; the LLM sometimes mislabels it ``analog`` because an official
    scenario path also fits the analog definition. Deterministic backstop to the
    prompt's direct/analog rule — only upgrades (never downgrades), and only on a
    sign-consistent, data-present factor, so it can't over-claim direct."""
    if not timeseries:
        return
    by_var: dict[str, dict[str, list[float]]] = {}
    for hit in timeseries:
        for r in hit.rows:
            var, scen = r.get("variable"), r.get("scenario") or ""
            try:
                val = float(r.get("value"))   # type: ignore[arg-type]  # None caught by except
            except (TypeError, ValueError):
                continue
            if var:
                by_var.setdefault(var, {}).setdefault(scen, []).append(val)

    for name, f in fields.items():
        if getattr(f, "grounding_mode", "direct") != "analog":
            continue
        var = RECONCILE_FIELD_TO_SERIES.get(name)
        if not var or var not in by_var or not _material(f):
            continue
        direction = _scenario_direction(by_var, var)
        if direction and _sign(f.value) == direction:
            f.grounding_mode = "direct"
            if diag is not None:
                diag.warn("grounding_upgraded", field=name, to="direct",
                          variable=var)


def validate_shock_vector(fields: dict[str, ShockField], *,
                          horizon_days: int | None = None,
                          timeseries: list[TimeseriesHit] | None = None,
                          reference_horizon_days: int = 90,
                          drop_invalid: bool = False,
                          diag: SynthesisDiagnostics | None = None
                          ) -> dict[str, ShockField]:
    """Annotate (and optionally prune) ``fields`` in place with numeric-validation
    and coherence codes. Returns the (possibly reduced) mapping.

    Per-field magnitude and series-anchor checks run first, then the pairwise
    coherence rules over whatever remains. With ``drop_invalid`` a field carrying
    a hard magnitude failure is removed and recorded as a diagnostics drop; all
    other flags are surface-only."""
    for f in fields.values():
        _check_magnitude(f, horizon_days, reference_horizon_days, diag)
        _check_series_anchor(f, timeseries, diag)
    _check_coherence(fields, diag)
    _check_rates_vol_term(fields, diag)

    if drop_invalid:
        for name in [n for n, f in fields.items()
                     if HARD_VALIDATION_CODES.intersection(f.validation)]:
            if diag is not None:
                diag.drop(name, "validate", "invalid_magnitude",
                          codes=list(fields[name].validation))
            del fields[name]
    return fields


def check_source_horizon(fields: dict[str, ShockField], chunks: list[RetrievedChunk],
                         *, horizon_days: int | None, tolerance: float,
                         diag: SynthesisDiagnostics | None = None) -> None:
    """Flag fields whose evidence describes a move over a very different horizon.

    Annotates ``fields`` in place (``horizon_mismatch``, ``horizon_unknown``) and
    records diagnostics warnings; never drops.

    Args:
        fields: The gated shock fields.
        chunks: The retrieved chunks the fields cite.
        horizon_days: The requested scenario horizon; None or <= 0 disables the check.
        tolerance: The largest allowed ratio between evidence and requested horizon.
        diag: Optional diagnostics sink.
    """
    if horizon_days is None or horizon_days <= 0:
        return
    req = horizon_days
    by_id = {c.chunk_id: c for c in chunks}
    for f in fields.values():
        exact: list[int] = []
        minimum: list[int] = []
        for cid in f.chunk_ids:
            c = by_id.get(cid)
            if c is None:
                continue
            h = c.payload.get("horizon_days")
            if _positive_int(h):
                exact.append(h)
            elif (m := DOC_TYPE_MIN_HORIZON_DAYS.get(str(c.payload.get("doc_type")))) is not None:
                minimum.append(m)
        d = f.source_horizon_days
        if exact or minimum:
            ok = (any(_ratio(h, req) <= tolerance for h in exact)
                  or any(req * tolerance >= m for m in minimum))
            flag = None if ok else ("horizon_mismatch", "chunk", sorted(set(exact + minimum)))
        elif _positive_int(d):
            flag = (("horizon_mismatch", "model", [d])
                    if _ratio(d, req) > tolerance else None)
        else:
            flag = ("horizon_unknown", "none", [])
        if flag is None or flag[0] in f.validation:
            continue
        code, basis, evidence = flag
        f.validation.append(code)
        if diag is not None:
            diag.warn(code, field=f.name, requested_days=req, basis=basis,
                      evidence_days=evidence)


def check_direction(fields: dict[str, ShockField], directions: dict[str, str] | None,
                    *, diag: SynthesisDiagnostics | None = None) -> None:
    """Flag fields whose sign contradicts the caller's requested direction.

    Annotates ``fields`` in place with ``direction_mismatch`` and records a
    diagnostics warning; never drops a field or changes a value (spec
    synth-direction).

    Args:
        fields: The gated shock fields.
        directions: Field name -> ``"up"`` (a positive value) or ``"down"`` (a
            negative value); None or empty disables the check.
        diag: Optional diagnostics sink.
    """
    if not directions:
        return
    for name, f in fields.items():
        wanted = directions.get(name)
        if not ((wanted == "up" and f.value < 0) or (wanted == "down" and f.value > 0)):
            continue
        if "direction_mismatch" in f.validation:
            continue
        f.validation.append("direction_mismatch")
        if diag is not None:
            diag.warn("direction_mismatch", field=name, requested=wanted, value=f.value)


def check_card_values(fields: dict[str, ShockField], chunks: list[RetrievedChunk],
                      *, diag: SynthesisDiagnostics | None = None) -> None:
    """Flag fields that cite an episode card but do not carry its largest move.

    Annotates ``fields`` in place with the hard code ``card_value_mismatch`` and
    records a diagnostics warning; never drops (spec synth-card-value).

    Args:
        fields: The gated shock fields.
        chunks: The retrieved chunks the fields cite.
        diag: Optional diagnostics sink.
    """
    peaks: dict[str, dict[str, float]] = {}      # chunk_id -> field -> largest move
    for c in chunks:
        if c.payload.get("doc_type") != "market_episode":
            continue
        for m in _CARD_LINE.finditer(c.text or ""):
            name = m.group("label").split(" ", 1)[0]
            if name in SHOCK_FIELDS:
                peaks.setdefault(c.chunk_id, {})[name] = float(m.group("peak"))
    for name, f in fields.items():
        cited = {cid: peaks[cid][name] for cid in f.chunk_ids
                 if cid in peaks and name in peaks[cid]}
        if not cited:
            continue
        tol = _CARD_TOLERANCE.get(f.unit, 0.05) + 1e-9
        if any(abs(f.value - p) <= tol for p in cited.values()):
            continue
        if "card_value_mismatch" not in f.validation:
            f.validation.append("card_value_mismatch")
        if diag is not None:
            diag.warn("card_value_mismatch", field=name, value=f.value, card_peaks=cited)


def check_episode_mix(fields: dict[str, ShockField], chunks: list[RetrievedChunk],
                      *, diag: SynthesisDiagnostics | None = None) -> None:
    """Flag a vector whose fields cite more than one market-episode card.

    Annotates card-citing fields in place with the soft code ``episode_mixed`` and
    records one diagnostics warning mapping episode -> fields; never drops (spec
    synth-vector-report).

    Args:
        fields: The kept shock fields.
        chunks: The retrieved chunks the fields cite.
        diag: Optional diagnostics sink.
    """
    episode_of = {c.chunk_id: c.chunk_id.split("::")[0] for c in chunks
                  if c.payload.get("doc_type") == "market_episode"}
    episodes: dict[str, list[str]] = {}
    for name, f in fields.items():
        for ep in {episode_of[cid] for cid in f.chunk_ids if cid in episode_of}:
            episodes.setdefault(ep, []).append(name)
    if len(episodes) < 2:
        return
    citing = {n for names in episodes.values() for n in names}
    for name in citing:
        if "episode_mixed" not in fields[name].validation:
            fields[name].validation.append("episode_mixed")
    if diag is not None:
        diag.warn("episode_mixed", episodes={ep: sorted(ns) for ep, ns in episodes.items()})


def _positive_int(x: object) -> TypeGuard[int]:
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def _ratio(h: int, req: int) -> float:
    """How many times apart two horizons are (>= 1)."""
    return max(h / req, req / h)


def check_analog_quality(fields: dict[str, ShockField], chunks: list[RetrievedChunk],
                         *, diag: SynthesisDiagnostics | None = None) -> None:
    """Flag unit/scale slips against the cited text and off-target FX proxies.

    Annotates ``fields`` in place (``unit_scale_mismatch``,
    ``fx_proxy_unlisted``) and records diagnostics warnings; never drops.

    Args:
        fields: The gated shock fields.
        chunks: The retrieved chunks the fields cite.
        diag: Optional diagnostics sink.
    """
    text_of = {c.chunk_id: c.text or "" for c in chunks}
    for f in fields.values():
        cited = [_quantity(m) for cid in f.chunk_ids if cid in text_of
                 for m in _QUANTITY.finditer(text_of[cid])]
        slip = _unit_slip(f, cited)
        if slip is not None and "unit_scale_mismatch" not in f.validation:
            f.validation.append("unit_scale_mismatch")
            if diag is not None:
                diag.warn("unit_scale_mismatch", field=f.name, value=f.value,
                          unit=f.unit, cited=abs(slip[0]), cited_unit=slip[1])
        named = _off_list_proxy(f)
        if named and "fx_proxy_unlisted" not in f.validation:
            f.validation.append("fx_proxy_unlisted")
            if diag is not None:
                diag.warn("fx_proxy_unlisted", field=f.name, named=named)


# ── analog quality (unit/scale slips, FX proxies) ────────────────────────────
# A number then a unit token, longest token first; word tokens end at a word
# boundary ("1 percentile" is not a percent).
_QUANTITY = re.compile(
    r"(?<![\d.])([-+]?\d+(?:\.\d+)?)\s*"
    r"(percentage\s+points?\b|pp\b|per\s*cent\b|percent\b|%|basis\s+points?\b|bps\b|bp\b"
    r"|index\s+points?\b|points?\b|pts\b)", re.IGNORECASE)

# Factor from a cited quantity kind to a field unit; a missing pair is ignored.
_UNIT_FACTOR: dict[str, dict[str, float]] = {
    "bp":  {"percent": 100.0, "pp": 100.0, "bp": 1.0},
    "pct": {"percent": 1.0, "pp": 1.0, "bp": 0.01},
    "pts": {"points": 1.0},
    "rel": {"percent": 0.01, "pp": 0.01},
}
_SCALE_MATCH_TOL = 0.05          # a cited quantity within 5% after conversion matches
_CURRENCY = re.compile(r"(?<![A-Za-z])[A-Z]{3}(?![A-Za-z])")


def _quantity(m: re.Match) -> tuple[float, str]:
    token = re.sub(r"\s+", " ", m.group(2).lower())
    if token.startswith("percentage") or token == "pp":
        kind = "pp"
    elif token.startswith("per") or token == "%":
        kind = "percent"
    elif token.startswith("basis") or token in ("bp", "bps"):
        kind = "bp"
    else:
        kind = "points"
    return float(m.group(1)), kind


def _unit_slip(f: ShockField, cited: list[tuple[float, str]]) -> tuple[float, str] | None:
    """The first cited quantity whose digits the value copied across a unit
    change (factor != 1), unless some cited quantity matches after conversion."""
    factors = _UNIT_FACTOR.get(f.unit, {})
    v = abs(f.value)
    if v == 0:
        return None
    if any(k in factors and abs(abs(q) * factors[k] - v) <= _SCALE_MATCH_TOL * v
           for q, k in cited):
        return None
    for q, k in cited:
        if k in factors and factors[k] != 1.0 and math.isclose(abs(q), v, rel_tol=1e-6):
            return q, k
    return None


def _off_list_proxy(f: ShockField) -> list[str]:
    """For an analog FX field: the currencies its rationale names (USD aside) when
    none is on the field's allowlist; otherwise empty."""
    allow = FX_PROXY_ALLOWLIST.get(f.name)
    if allow is None or f.grounding_mode != "analog":
        return []
    named = sorted({c for c in _CURRENCY.findall(f.rationale or "")
                    if c in FX_CODES and c != "USD"})
    return named if named and not allow.intersection(named) else []
