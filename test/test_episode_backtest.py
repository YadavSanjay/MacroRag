"""Realized-episode backtest (Loop 4): period arithmetic, references derived from
realized history, point-in-time retrieval bounds, and the two-mode runner whose
metrics must never merge. All fakes — no LLM, no network."""

from __future__ import annotations

from eval.episodes import (
    EPISODES,
    EPISODES_BY_KEY,
    EX_ANTE_SKILL,
    GROUNDING_FIDELITY,
    Episode,
    _horizon_days,
    _spread,
    derive_realized_reference,
    point_in_time_cutoff,
    point_in_time_filter,
    quarter_start_date,
    run_episode_backtest,
    shift_quarters,
)
from synthesis.schema import ShockField
from utils.schema import MetaFilter
from vectorstores.timeseries_store import TimeseriesStore

# ── fixtures ─────────────────────────────────────────────────────────────────

_EP = Episode(key="test", aka="Test episode", scenario="A shock happens.",
              start="2008-Q3", end="2008-Q4", pre_quarters=2)


def _store(tmp_path, series: dict[str, dict[str, float]]) -> TimeseriesStore:
    """series = {variable: {period: value}} under DFAST_2026 / historic."""
    rows = [{"source": "DFAST_2026", "scenario": "historic", "variable": var,
             "period": period, "value": value, "publisher": "frb", "tier": 1}
            for var, points in series.items()
            for period, value in points.items()]
    st = TimeseriesStore(tmp_path / "ts.sqlite")
    st.build(rows)
    return st


def _flat(value: float, periods: list[str]) -> dict[str, float]:
    return {p: value for p in periods}


_PRE = ["2008-Q1", "2008-Q2"]
_DURING = ["2008-Q3", "2008-Q4"]


# ── period arithmetic ────────────────────────────────────────────────────────

def test_shift_quarters_across_year_boundaries():
    assert shift_quarters("2008-Q3", -4) == "2007-Q3"
    assert shift_quarters("2008-Q3", -1) == "2008-Q2"
    assert shift_quarters("2008-Q1", -1) == "2007-Q4"
    assert shift_quarters("2018-Q4", 1) == "2019-Q1"
    assert shift_quarters("2020-Q1", 0) == "2020-Q1"


def test_quarter_start_dates():
    assert quarter_start_date("2008-Q1").isoformat() == "2008-01-01"
    assert quarter_start_date("2008-Q3").isoformat() == "2008-07-01"
    assert quarter_start_date("2008-Q4").isoformat() == "2008-10-01"


def test_periods_sort_lexicographically():
    # the windowing relies on this, so pin it
    assert sorted(["2009-Q1", "2008-Q3", "2008-Q10"[:7], "1999-Q4"])[0] == "1999-Q4"
    assert "2008-Q3" < "2009-Q1" and "2008-Q1" < "2008-Q3"


def test_horizon_covers_the_measured_window():
    assert _horizon_days(Episode("k", "a", "s", "2018-Q4", "2018-Q4")) == 92
    assert _horizon_days(Episode("k", "a", "s", "2022-Q1", "2022-Q4")) == 365


# ── point-in-time bound ──────────────────────────────────────────────────────

def test_pit_cutoff_is_the_day_before_the_window_opens():
    assert point_in_time_cutoff(_EP) == "2008-06-30"
    assert point_in_time_cutoff(
        Episode("k", "a", "s", "2020-Q1", "2020-Q2")) == "2019-12-31"


def test_pit_filter_sets_date_to_and_preserves_the_base_filter():
    base = MetaFilter(tiers=[1, 2], factor_tags=["credit_spread"])
    mf = point_in_time_filter(_EP, base)
    assert mf.date_to == "2008-06-30"
    assert mf.tiers == [1, 2] and mf.factor_tags == ["credit_spread"]
    assert base.date_to is None          # the caller's filter is not mutated


def test_pit_filter_without_a_base():
    assert point_in_time_filter(_EP).date_to == "2008-06-30"


# ── realized reference derivation ────────────────────────────────────────────

def test_reference_measures_peak_deviation_from_the_pre_window(tmp_path):
    st = _store(tmp_path, {
        "vix": {**_flat(20.0, _PRE), "2008-Q3": 30.0, "2008-Q4": 70.0},
    })
    ref = derive_realized_reference(st, _EP)
    assert ref["VIX"]["unit"] == "pts"
    assert ref["VIX"]["value"] == 50.0        # peak 70 vs pre-level 20


def test_reference_scales_rate_levels_into_basis_points(tmp_path):
    st = _store(tmp_path, {
        "treasury_10y": {**_flat(4.0, _PRE), "2008-Q3": 3.5, "2008-Q4": 2.9},
    })
    ref = derive_realized_reference(st, _EP)
    assert ref["UST_10Y"]["unit"] == "bp"
    assert ref["UST_10Y"]["value"] == -110.0   # -1.10% -> -110bp, sign preserved


def test_reference_builds_a_spread_field(tmp_path):
    st = _store(tmp_path, {
        "bbb_corporate_yield": {**_flat(6.0, _PRE), "2008-Q3": 8.0, "2008-Q4": 11.0},
        "treasury_10y": {**_flat(4.0, _PRE), "2008-Q3": 3.5, "2008-Q4": 2.9},
    })
    ref = derive_realized_reference(st, _EP)
    # pre spread 2.0; peak spread 11.0-2.9 = 8.1 -> +610bp
    assert ref["IG_OAS"]["unit"] == "bp" and ref["IG_OAS"]["value"] == 610.0


def test_spread_aligns_on_period_not_position():
    # the two series start in different quarters; zipping raw lists would
    # silently subtract mismatched periods
    minuend = [("2008-Q1", 6.0), ("2008-Q2", 7.0)]
    subtrahend = [("2007-Q4", 99.0), ("2008-Q1", 4.0), ("2008-Q2", 4.5)]
    assert _spread(minuend, subtrahend) == [("2008-Q1", 2.0), ("2008-Q2", 2.5)]


def test_fields_without_realized_data_are_omitted_not_guessed(tmp_path):
    st = _store(tmp_path, {"vix": {**_flat(20.0, _PRE), "2008-Q3": 60.0}})
    ref = derive_realized_reference(st, _EP)
    assert set(ref) == {"VIX"}               # no rates data -> no rates fields


def test_reference_empty_when_the_window_has_no_data(tmp_path):
    st = _store(tmp_path, {"vix": _flat(20.0, ["1999-Q1", "1999-Q2"])})
    assert derive_realized_reference(st, _EP) == {}


def test_tolerance_uses_the_unit_floor_for_small_moves(tmp_path):
    st = _store(tmp_path, {
        "treasury_10y": {**_flat(4.0, _PRE), "2008-Q3": 4.05, "2008-Q4": 4.1},
    })
    ref = derive_realized_reference(st, _EP)
    assert ref["UST_10Y"]["value"] == 10.0    # +10bp
    assert ref["UST_10Y"]["tol"] == 25.0      # floor, not 50% of 10bp


def test_tolerance_is_proportional_for_large_moves(tmp_path):
    st = _store(tmp_path, {"vix": {**_flat(20.0, _PRE), "2008-Q4": 120.0}})
    ref = derive_realized_reference(st, _EP)
    assert ref["VIX"]["tol"] == 50.0          # 50% of a 100pt move


# ── episode catalog ──────────────────────────────────────────────────────────

def test_catalog_is_well_formed():
    assert len(EPISODES) >= 5
    for ep in EPISODES:
        assert ep.start <= ep.end
        assert EPISODES_BY_KEY[ep.key] is ep
        # the question must not leak the outcome it is scored against
        assert "basis point" not in ep.scenario.lower()
        assert not any(ch.isdigit() for ch in ep.scenario)


# ── runner ───────────────────────────────────────────────────────────────────

def _two_field_store(tmp_path):
    return _store(tmp_path, {
        "vix": {**_flat(20.0, _PRE), "2008-Q3": 30.0, "2008-Q4": 70.0},
        "treasury_10y": {**_flat(4.0, _PRE), "2008-Q3": 3.5, "2008-Q4": 2.9},
    })


def test_runner_keeps_the_two_modes_apart(tmp_path):
    st = _two_field_store(tmp_path)

    def synth(question, *, horizon_days=None, base_filter=None):
        # perfect when ungated, silent under the point-in-time bound
        if base_filter is not None and base_filter.date_to:
            return {}
        return {"VIX": ShockField("VIX", 50.0, "pts", ["c1"]),
                "UST_10Y": ShockField("UST_10Y", -110.0, "bp", ["c1"])}

    out = run_episode_backtest(st, synth, episodes=[_EP])
    s = out["summary"]
    assert s["grounding_fidelity_tolerance_rate"] == 1.0
    assert s["grounding_fidelity_abstention_rate"] == 0.0
    assert s["ex_ante_skill_abstention_rate"] == 1.0
    assert s["ex_ante_skill_n_abstained"] == 1
    # the two must never appear under one merged key
    assert "tolerance_rate" not in s


def test_runner_applies_the_pit_bound_only_in_ex_ante_mode(tmp_path):
    st = _two_field_store(tmp_path)
    seen: list[object] = []

    def synth(question, *, horizon_days=None, base_filter=None):
        seen.append(base_filter)
        return {}

    run_episode_backtest(st, synth, episodes=[_EP])
    grounded, ex_ante = seen
    assert grounded is None
    assert ex_ante.date_to == "2008-06-30"


def test_runner_scores_a_wrong_sign_as_a_miss(tmp_path):
    st = _two_field_store(tmp_path)

    def synth(question, *, horizon_days=None, base_filter=None):
        # right magnitude, wrong direction on rates (the 2022-style trap)
        return {"UST_10Y": ShockField("UST_10Y", 110.0, "bp", ["c1"])}

    out = run_episode_backtest(st, synth, episodes=[_EP],
                               modes=[GROUNDING_FIDELITY])
    row = out["modes"][GROUNDING_FIDELITY]["rows"][0]
    assert row["fields"]["UST_10Y"]["sign_match"] is False
    assert row["fields"]["UST_10Y"]["within_tol"] is False
    assert out["summary"]["grounding_fidelity_sign_accuracy"] == 0.0


def test_runner_marks_abstention_on_the_row(tmp_path):
    st = _two_field_store(tmp_path)
    out = run_episode_backtest(st, lambda q, **kw: {}, episodes=[_EP],
                               modes=[EX_ANTE_SKILL])
    row = out["modes"][EX_ANTE_SKILL]["rows"][0]
    assert row["abstained"] is True and row["pit_cutoff"] == "2008-06-30"


def test_runner_reports_the_derived_reference_per_episode(tmp_path):
    out = run_episode_backtest(_two_field_store(tmp_path), lambda q, **kw: {},
                               episodes=[_EP], modes=[GROUNDING_FIDELITY])
    ep = out["episodes"][0]
    assert ep["key"] == "test" and ep["fields"] == ["UST_10Y", "VIX"]
    assert ep["reference"]["VIX"]["value"] == 50.0
    assert ep["pit_cutoff"] == "2008-06-30"


def test_runner_skips_episodes_with_no_realized_reference(tmp_path):
    empty = _store(tmp_path, {"vix": _flat(20.0, ["1999-Q1"])})
    out = run_episode_backtest(empty, lambda q, **kw: {}, episodes=[_EP])
    assert out["modes"][GROUNDING_FIDELITY]["rows"] == []
    assert out["episodes"][0]["fields"] == []


def test_runner_passes_the_episode_horizon(tmp_path):
    seen: list[int] = []

    def synth(question, *, horizon_days=None, base_filter=None):
        seen.append(horizon_days)
        return {}

    run_episode_backtest(_two_field_store(tmp_path), synth, episodes=[_EP],
                         modes=[GROUNDING_FIDELITY])
    assert seen == [184]           # 2008-Q3..2008-Q4


def test_runner_cancellation_propagates(tmp_path):
    class Cancelled(RuntimeError):
        pass

    def cancel():
        raise Cancelled()

    try:
        run_episode_backtest(_two_field_store(tmp_path), lambda q, **kw: {},
                             episodes=[_EP], should_cancel=cancel)
    except Cancelled:
        return
    raise AssertionError("should_cancel was never honoured")


# ── EQUITY_US grounding (D4): an index level becomes a percent move ──────────

def test_equity_reference_is_a_percent_of_the_pre_crisis_level(tmp_path):
    st = _store(tmp_path, {
        "dow_jones": {**_flat(20000.0, _PRE), "2008-Q3": 17000.0, "2008-Q4": 12000.0},
    })
    ref = derive_realized_reference(st, _EP)
    assert ref["EQUITY_US"]["unit"] == "pct"
    assert ref["EQUITY_US"]["value"] == -40.0     # 12000 vs 20000, not -8000 points


def test_equity_reference_needs_a_nonzero_base(tmp_path):
    st = _store(tmp_path, {
        "dow_jones": {**_flat(0.0, _PRE), "2008-Q3": 100.0, "2008-Q4": 200.0},
    })
    # dividing by a zero base would emit an infinite percent move; omit instead
    assert "EQUITY_US" not in derive_realized_reference(st, _EP)


def test_absolute_unit_fields_are_not_relativised(tmp_path):
    st = _store(tmp_path, {"vix": {**_flat(20.0, _PRE), "2008-Q4": 70.0}})
    assert derive_realized_reference(st, _EP)["VIX"]["value"] == 50.0
