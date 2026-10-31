"""Realized-episode backtest (validation plan, Loop 4).

``shock_eval`` scores an emitted vector against *projected* official paths — the
baseline→adverse delta of a DFAST/EBA scenario. That measures whether we
transcribed the Fed's scenario correctly. It does **not** measure whether the
numbers match what markets actually did, and conflating the two was the honest
weakness in the pitch (marketing brief §D).

This module closes that gap using data already in the store: the DFAST ingest
carries a ``historic`` scenario holding 50 years of realized quarterly macro
history (1976-Q1 → 2025-Q4, 28 variables). Define a crisis window, reduce the
realized path to a peak deviation from its pre-crisis level, and you have a
reference built from what *happened* rather than what was *projected*.

It runs in two modes whose metrics are deliberately kept apart, because merging
them is exactly the overclaim this exists to prevent:

``grounding_fidelity``
    No date filter. Can the system turn a described scenario into the right
    numbers when the evidence is retrievable? This is what the product claims,
    and it is a legitimate, defensible measure.

``ex_ante_skill``
    Retrieval filtered to ``release_date`` strictly before the episode opens, so
    only documents that existed at the time are visible. This measures genuine
    forecast skill — and it is **bounded by corpus depth, not by model quality**.
    The corpus is mostly recent, so for older episodes there is little or nothing
    to retrieve and the system correctly abstains. A high ``abstention_rate``
    here is an honest statement about the corpus, NOT a quality failure, and the
    runner reports it separately so it can never be read as one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from utils.logging import get_logger
from utils.schema import MetaFilter
from vectorstores.timeseries_store import TimeseriesStore

from . import shock_metrics

# The peak-deviation reducer and the field mapping are shared with the projected
# -path backtest on purpose: the two references must be computed the same way or
# their numbers aren't comparable.
from .shock_eval import DEFAULT_STORE_MAPPING, _reduce_delta, _relativise

log = get_logger(__name__)

SynthFn = Callable[..., dict]

GROUNDING_FIDELITY = "grounding_fidelity"
EX_ANTE_SKILL = "ex_ante_skill"
MODES = (GROUNDING_FIDELITY, EX_ANTE_SKILL)

# Where the realized history lives (the DFAST file's own `historic` block).
HISTORIC_SOURCE_LIKE = "DFAST"
HISTORIC_SCENARIO = "historic"

# Minimum tolerance band per unit. A realized-episode reference is testing sign
# and order of magnitude, not calibration, so the band is deliberately wide —
# below these floors a proportional band would be unfairly tight on a small move.
_TOL_FLOOR: dict[str, float] = {"bp": 25.0, "pts": 5.0, "pct": 2.0, "rel": 0.25}

_QUARTER_START_MONTH = {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}


# ── episode catalog ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Episode:
    """One realized stress window.

    ``scenario`` describes the shock *mechanism* without naming its outcome, so
    the same text is fair in both modes — in ex-ante mode a question that quoted
    the realized move would leak the answer."""
    key: str
    aka: str                  # display name
    scenario: str             # the question put to the synthesizer
    start: str                # inclusive, "2008-Q3"
    end: str                  # inclusive, "2009-Q1"
    pre_quarters: int = 4     # quarters before `start` that set the reference level


EPISODES: tuple[Episode, ...] = (
    Episode(
        key="gfc_2008", aka="Global financial crisis (2008-09)",
        scenario=("A systemic banking crisis: major financial institutions fail, "
                  "short-term funding markets freeze, credit spreads gap wider and "
                  "risk assets sell off sharply."),
        start="2008-Q3", end="2009-Q1"),
    Episode(
        key="covid_2020", aka="COVID-19 shock (2020)",
        scenario=("A global pandemic triggers simultaneous supply and demand "
                  "shocks, with widespread lockdowns, a collapse in activity and "
                  "an abrupt flight to quality."),
        start="2020-Q1", end="2020-Q2"),
    Episode(
        key="inflation_2022", aka="Inflation shock and tightening cycle (2022)",
        scenario=("Inflation rises to multi-decade highs and the central bank "
                  "tightens policy aggressively, repricing the entire yield curve "
                  "and compressing valuations."),
        start="2022-Q1", end="2022-Q4"),
    Episode(
        key="euro_debt_2011", aka="Euro-area sovereign debt crisis (2011)",
        scenario=("A euro-area sovereign debt crisis raises contagion and "
                  "redenomination risk, straining bank funding and driving a "
                  "flight into core government bonds."),
        start="2011-Q3", end="2011-Q4"),
    Episode(
        key="risk_off_2018", aka="Q4 2018 risk-off repricing",
        scenario=("A sharp risk-off repricing as policy tightens into slowing "
                  "growth: equities fall, volatility spikes and credit widens."),
        start="2018-Q4", end="2018-Q4"),
    Episode(
        key="dotcom_2001", aka="Dot-com bust and 2001 recession",
        scenario=("A technology-equity bubble unwinds, capital spending "
                  "contracts and the economy tips into recession."),
        start="2001-Q1", end="2001-Q4"),
)

EPISODES_BY_KEY = {e.key: e for e in EPISODES}


# ── period helpers ───────────────────────────────────────────────────────────
# Periods are "YYYY-Qn", which sorts lexicographically in true chronological
# order, so plain string comparison is correct for windowing.


def quarter_start_date(period: str) -> date:
    year, _, q = str(period).partition("-")
    return date(int(year), _QUARTER_START_MONTH[q.upper()], 1)


def shift_quarters(period: str, n: int) -> str:
    """``('2008-Q3', -4) -> '2007-Q3'``."""
    year, _, q = str(period).partition("-")
    index = int(year) * 4 + (_QUARTER_START_MONTH[q.upper()] - 1) // 3 + n
    return f"{index // 4}-Q{index % 4 + 1}"


def point_in_time_cutoff(episode: Episode) -> str:
    """ISO date of the last day before the episode opens — the ``date_to`` bound
    that makes retrieval blind to anything published during or after it."""
    return (quarter_start_date(episode.start) - timedelta(days=1)).isoformat()


def point_in_time_filter(episode: Episode,
                         base: MetaFilter | None = None) -> MetaFilter:
    mf = MetaFilter(**vars(base)) if base is not None else MetaFilter()
    mf.date_to = point_in_time_cutoff(episode)
    return mf


# ── realized reference ───────────────────────────────────────────────────────


def _series(store: TimeseriesStore, variable: str, *,
            source_like: str = HISTORIC_SOURCE_LIKE,
            scenario: str = HISTORIC_SCENARIO) -> list[tuple[str, float]]:
    """(period, value) pairs for a realized series, chronologically ordered."""
    rows = store.query(source_like=source_like, scenario=scenario,
                       variable=variable, limit=5000)
    out = [(str(r["period"]), float(r["value"])) for r in rows
           if r.get("period") and r.get("value") is not None]
    out.sort(key=lambda pv: pv[0])
    return out


def _slice(series: list[tuple[str, float]], start: str, end: str) -> list[float]:
    return [v for p, v in series if start <= p <= end]


def _spread(minuend: list[tuple[str, float]],
            subtrahend: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Per-period difference of two series, aligned on period (not on position —
    the two may start in different years, and zipping raw lists would silently
    subtract mismatched quarters)."""
    lookup = dict(subtrahend)
    return [(p, v - lookup[p]) for p, v in minuend if p in lookup]


def _tolerance(value: float, unit: str, tol_frac: float) -> float:
    return round(max(abs(value) * tol_frac, _TOL_FLOOR.get(unit, 0.0)), 4)


def derive_realized_reference(store: TimeseriesStore, episode: Episode, *,
                              mapping: dict[str, dict] | None = None,
                              reduce: str = "peak",
                              peak_percentile: float = 90.0,
                              tol_frac: float = 0.5,
                              source_like: str = HISTORIC_SOURCE_LIKE,
                              scenario: str = HISTORIC_SCENARIO
                              ) -> dict[str, dict]:
    """``{FIELD: {value, unit, tol}}`` from what actually happened in the window.

    The pre-episode quarters set the reference level; the in-window path is
    reduced to its peak deviation from that level (same reducer as the projected
    -path backtest). Fields whose series has no data in either window are
    omitted rather than guessed — the runner reports which were derivable."""
    mapping = mapping or DEFAULT_STORE_MAPPING
    pre_start = shift_quarters(episode.start, -episode.pre_quarters)
    pre_end = shift_quarters(episode.start, -1)

    ref: dict[str, dict] = {}
    for field, spec in mapping.items():
        kind = spec.get("kind")
        if kind == "level":
            series = _series(store, spec["variable"], source_like=source_like,
                             scenario=scenario)
        elif kind == "spread":
            series = _spread(
                _series(store, spec["minuend"], source_like=source_like,
                        scenario=scenario),
                _series(store, spec["subtrahend"], source_like=source_like,
                        scenario=scenario))
        else:
            log.warning("unknown mapping kind %r for %s", kind, field)
            continue

        pre = _slice(series, pre_start, pre_end)
        during = _slice(series, episode.start, episode.end)
        delta = _reduce_delta(pre, during, reduce=reduce,
                              peak_percentile=peak_percentile)
        if delta is None:
            log.info("episode %s: no realized data for %s", episode.key, field)
            continue
        # An index-level series (dow_jones) becomes a percent move against its
        # own pre-crisis level; absolute-unit fields pass through unchanged.
        delta = _relativise(delta, pre, spec)
        if delta is None:
            continue
        delta *= float(spec.get("scale", 1.0))
        unit = spec["unit"]
        ref[field] = {"value": round(delta, 4), "unit": unit,
                      "tol": _tolerance(delta, unit, tol_frac)}
    return ref


# ── runner ───────────────────────────────────────────────────────────────────


def _horizon_days(episode: Episode) -> int:
    """Episode length in days, so the synthesizer sizes the move over the same
    window the reference was measured over."""
    start = quarter_start_date(episode.start)
    end = quarter_start_date(shift_quarters(episode.end, 1))
    return (end - start).days


def run_episode_backtest(store: TimeseriesStore, synth_fn: SynthFn, *,
                         episodes: Iterable[Episode] | None = None,
                         modes: Iterable[str] = MODES,
                         base_filter: MetaFilter | None = None,
                         tol_frac: float = 0.5,
                         should_cancel: Callable[[], None] | None = None,
                         on_progress: Callable[[str], None] | None = None
                         ) -> dict[str, Any]:
    """Score synthesized vectors against realized episode moves, per mode.

    Returns ``{summary, modes, episodes}``. Summary keys are **prefixed by mode**
    (``grounding_fidelity_sign_accuracy``, ``ex_ante_skill_sign_accuracy``, …) so
    the two can never be silently averaged into one number."""
    eps = list(episodes) if episodes is not None else list(EPISODES)
    wanted = [m for m in modes if m in MODES]

    def note(msg: str) -> None:
        if on_progress is not None:
            on_progress(msg)
        log.info("episode-backtest: %s", msg)

    references: dict[str, dict] = {}
    catalog = []
    for ep in eps:
        references[ep.key] = derive_realized_reference(store, ep,
                                                       tol_frac=tol_frac)
        catalog.append({"key": ep.key, "aka": ep.aka, "start": ep.start,
                        "end": ep.end, "scenario": ep.scenario,
                        "fields": sorted(references[ep.key]),
                        "reference": references[ep.key],
                        "pit_cutoff": point_in_time_cutoff(ep)})
        note(f"{ep.key}: derived {len(references[ep.key])} realized field(s) "
             f"from {ep.start}..{ep.end}")

    out_modes: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    for mode in wanted:
        rows, summaries, abstained = [], [], 0
        for ep in eps:
            if should_cancel is not None:
                should_cancel()
            reference = references[ep.key]
            if not reference:
                note(f"{ep.key}: no realized reference; skipped")
                continue
            filt = (point_in_time_filter(ep, base_filter)
                    if mode == EX_ANTE_SKILL else base_filter)
            emitted = synth_fn(ep.scenario, horizon_days=_horizon_days(ep),
                               base_filter=filt) or {}
            scored = shock_metrics.score_shock_vector(emitted, reference)
            if scored["summary"]["n_emitted"] == 0:
                abstained += 1
            summaries.append(scored["summary"])
            rows.append({"episode": ep.key, "aka": ep.aka, "mode": mode,
                         "scenario": ep.scenario,
                         "pit_cutoff": point_in_time_cutoff(ep)
                         if mode == EX_ANTE_SKILL else None,
                         "abstained": scored["summary"]["n_emitted"] == 0,
                         **scored})

        agg = shock_metrics.aggregate_shock(summaries)
        n = len(summaries)
        # Reported alongside accuracy, never folded into it: in ex-ante mode an
        # abstention usually means the corpus had nothing published in time.
        agg["n_abstained"] = abstained
        agg["abstention_rate"] = (abstained / n) if n else 0.0
        out_modes[mode] = {"summary": agg, "rows": rows}
        for key, value in agg.items():
            summary[f"{mode}_{key}"] = value
        note(f"{mode}: {n} episode(s), {abstained} abstained")

    return {"summary": summary, "modes": out_modes, "episodes": catalog}
