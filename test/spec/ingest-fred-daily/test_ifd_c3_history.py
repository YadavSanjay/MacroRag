"""C3: re-fetching never shortens stored history (metamorphic)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

RECENT = 2025          # D.year - 1 for the fixed today 2026-10-14
DEFAULT_START = 2005   # year of the fixture endpoint's default backfill_start

ICE = [("2020-01-02", "1.10"), ("2020-06-30", "1.20"), ("2020-12-31", "1.30"),
       ("2021-01-04", "0.90"), ("2021-12-31", "0.95"),
       ("2022-01-03", "1.00"), ("2022-12-30", "1.40"),
       ("2023-01-03", "1.35"), ("2023-10-16", "1.60"), ("2023-12-29", "1.04"),
       ("2024-01-02", "1.00"), ("2024-12-31", "0.82"),
       ("2025-01-02", "0.80"), ("2025-12-31", "0.78"),
       ("2026-01-02", "0.79"), ("2026-10-13", "0.75")]


def _sizes(fred, docs) -> dict[str, int]:
    return {k: len(d.timeseries) for k, d in fred.by_id(docs).items()}


def _year(doc_id: str) -> int:
    return int(doc_id.rsplit("_", 1)[1])


def _assert_never_shorter(first: dict, second: dict, start_year: int):
    """Spec C3 relation; exempt: Y >= D.year-1 and the backfill_start year."""
    for doc_id, n in second.items():
        y = _year(doc_id)
        if doc_id in first and y < RECENT and y != start_year:
            assert n >= first[doc_id], (doc_id, n, first[doc_id])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_rolling_window_cut_does_not_overwrite_old_years(fred):
    # oracle: hand-mapped metamorphic pair (the ICE BofA case), backfill_start
    # 2005-01-01. Run 1 (backfill) sees years 2020..2026: 2020 is old, not the
    # backfill_start year and has no earlier row in the response -> window-cut,
    # not emitted; 2021..2024 each have an earlier row -> emitted; 2025, 2026 are
    # not old -> 6 partitions. Run 2 sees the same response with everything before
    # 2023-10-16 removed: 2020-2022 are absent, 2023 now has no earlier row
    # (window-cut -> not emitted), 2024 has 2023's rows before it, so 2024-2026 are
    # emitted with the same row counts as run 1 (2, 2, 2).
    cfg = fred.endpoint([("BAMLC0A0CM", "percent")])
    first, _, _ = fred.run(cfg, fred.FakeFredClient({"BAMLC0A0CM": ICE}), refresh=True)
    cut = [(d, v) for d, v in ICE if d >= "2023-10-16"]
    second, _, _ = fred.run(cfg, fred.FakeFredClient({"BAMLC0A0CM": cut}), refresh=True)

    s1, s2 = _sizes(fred, first), _sizes(fred, second)
    assert s1 == {"fred_daily_bamlc0a0cm_2021": 2,
                  "fred_daily_bamlc0a0cm_2022": 2, "fred_daily_bamlc0a0cm_2023": 3,
                  "fred_daily_bamlc0a0cm_2024": 2, "fred_daily_bamlc0a0cm_2025": 2,
                  "fred_daily_bamlc0a0cm_2026": 2}
    assert s2 == {"fred_daily_bamlc0a0cm_2024": 2, "fred_daily_bamlc0a0cm_2025": 2,
                  "fred_daily_bamlc0a0cm_2026": 2}
    _assert_never_shorter(s1, s2, DEFAULT_START)


@pytest.mark.criterion("C3")
def test_routine_run_emits_nothing_before_last_year(fred):
    # oracle: spec C3 — a routine run requests observation_start = D.year-1 =
    # 2025-01-01, so of the full ICE history only the 2025 and 2026 partitions are
    # emitted. Removing the response's earliest months (here: all of 2025 before
    # December) may shorten only Y >= D.year-1 partitions.
    cfg = fred.endpoint([("BAMLH0A0HYM2", "percent")])
    first, _, c1 = fred.run(cfg, fred.FakeFredClient({"BAMLH0A0HYM2": ICE}),
                            refresh=False)
    assert [r["params"].get("observation_start") for r in c1.requests] == ["2025-01-01"]
    s1 = _sizes(fred, first)
    assert s1 == {"fred_daily_bamlh0a0hym2_2025": 2, "fred_daily_bamlh0a0hym2_2026": 2}

    cut = [(d, v) for d, v in ICE if d >= "2025-12-01"]
    second, _, _ = fred.run(cfg, fred.FakeFredClient({"BAMLH0A0HYM2": cut}),
                            refresh=False)
    s2 = _sizes(fred, second)
    assert s2 == {"fred_daily_bamlh0a0hym2_2025": 1, "fred_daily_bamlh0a0hym2_2026": 2}
    _assert_never_shorter(s1, s2, DEFAULT_START)


_DAY0 = date(2015, 1, 1)
_SPAN = (date(2026, 10, 13) - _DAY0).days


def _expected(days: list[date], refresh: bool) -> dict[str, int]:
    """Closed form of the spec: partitions per year, minus window-cut old years.

    ``days`` is the response as served (all after backfill_start 2005-01-01). An
    old year (Y < D.year-1) is kept only when complete at its start: the response
    has a row before Y-01-01, or Y is the backfill_start year and starts by
    Y-01-10 (2005: no data here, so only the earlier-row branch can apply).
    """
    if not refresh:
        days = [d for d in days if d >= date(RECENT, 1, 1)]
    years: dict[int, list[date]] = {}
    for d in days:
        years.setdefault(d.year, []).append(d)
    return {f"fred_daily_dgs30_{y}": len(ds) for y, ds in years.items()
            if y >= RECENT
            or min(days) < date(y, 1, 1)
            or (y == DEFAULT_START and min(ds) <= date(y, 1, 10))}


@pytest.mark.criterion("C3")
@given(
    offsets=st.sets(st.integers(min_value=0, max_value=_SPAN), min_size=1, max_size=60),
    jan=st.sets(st.integers(min_value=2015, max_value=2026), max_size=12),
    cut=st.integers(min_value=0, max_value=_SPAN),
    refresh=st.booleans(),
)
# Pinned: the `tdd verify` counterexample to the first (01-10 only) rule. 2015 has
# rows 01-01 and 01-05; cutting at 01-02 left 01-05, which the old rule rewrote
# shorter (2 -> 1). Under the amended rule 2015 has no earlier row and is not the
# backfill_start year, so neither run emits it.
@example(offsets={0}, jan={2015}, cut=1, refresh=True)
def test_property_dropping_earliest_months_never_shortens_old_years(
        fred, offsets, jan, cut, refresh):
    # oracle: the spec's metamorphic relation. Run on a response, then on the same
    # response with every observation before a cut date removed: no partition of
    # the second run with Y < D.year-1, other than the backfill_start year's, has
    # fewer rows than in the first. Each run's partition set is checked against
    # the spec's closed form (`_expected`).
    days = sorted({_DAY0 + timedelta(days=o) for o in offsets}
                  | {date(y, 1, 5) for y in jan})
    obs = [(d.isoformat(), f"{i % 7}.25") for i, d in enumerate(days)]
    cut_day = _DAY0 + timedelta(days=cut)
    kept = [(d, v) for d, v in obs if d >= cut_day.isoformat()]
    cfg = fred.endpoint([("DGS30", "percent")])

    first, _, _ = fred.run(cfg, fred.FakeFredClient({"DGS30": obs}), refresh=refresh)
    second, _, _ = fred.run(cfg, fred.FakeFredClient({"DGS30": kept}), refresh=refresh)
    s1, s2 = _sizes(fred, first), _sizes(fred, second)
    assert s1 == _expected(days, refresh)
    assert s2 == _expected([d for d in days if d >= cut_day], refresh)
    if not refresh:
        assert all(_year(k) >= RECENT for k in s1 | s2)
    _assert_never_shorter(s1, s2, DEFAULT_START)
