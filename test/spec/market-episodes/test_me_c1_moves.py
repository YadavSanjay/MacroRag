"""C1: episode_moves returns each canonical series' realized move over k trading days."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ingest.usstats.episodes import episode_moves

# COVID-like case, anchor Friday 2020-02-21. Trading dates after the anchor:
_D = ["2020-02-24", "2020-02-25", "2020-02-26", "2020-02-27", "2020-02-28",
      "2020-03-02", "2020-03-03", "2020-03-04", "2020-03-05", "2020-03-06"]
_ANCHOR = date(2020, 2, 21)


def _series(me, code, units, base: tuple[str, float] | list[tuple[str, float]],
            values, dates=None):
    rows = [me.row(code, d, v, units) for d, v in ([base] if isinstance(base, tuple) else base)]
    rows += [me.row(code, d, v, units) for d, v in zip(dates or _D, values, strict=False)]
    return rows


def _covid_rows(me) -> list[dict]:
    rows = []
    # VIXCLS index_points, v0 = 17.00 on 02-21; 03-09 lies beyond the 10-day window.
    rows += _series(me, "VIXCLS", "index_points", ("2020-02-21", 17.00),
                    [25.00, 27.50, 27.00, 39.00, 36.00, 33.50, 36.50, 31.50, 39.50,
                     38.00, 54.50], _D + ["2020-03-09"])
    # DGS10 percent, latest row on/before the anchor is 02-14: exactly 7 days -> allowed.
    rows += _series(me, "DGS10", "percent", ("2020-02-14", 1.50),
                    [1.40, 1.35, 1.30, 1.05, 1.10, 1.15, 1.00, 0.95, 0.90, 0.75])
    # DGS2 percent, latest row on/before the anchor is 02-13: 8 days -> no move at all.
    rows += _series(me, "DGS2", "percent", ("2020-02-13", 1.40),
                    [1.30, 1.20, 1.10, 1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.40])
    # DGS5 percent, only 7 rows after the anchor: move at 5, none at 10.
    rows += _series(me, "DGS5", "percent", ("2020-02-21", 1.40),
                    [1.35, 1.30, 1.32, 1.20, 1.25, 1.22, 1.18])
    # DEXJPUS jpy_per_usd, own dates (no 02-24 row, extra 03-09 row).
    rows += _series(me, "DEXJPUS", "jpy_per_usd", ("2020-02-21", 112.00),
                    [110.88, 110.32, 109.76, 108.64, 109.20, 106.40, 107.52, 106.96,
                     105.28, 102.48], _D[1:] + ["2020-03-09"])
    # BAA10Y percent with no BAMLC0A0CM rows: the IG_OAS stand-in at both horizons.
    rows += _series(me, "BAA10Y", "percent", ("2020-02-21", 2.00),
                    [2.05, 2.10, 2.08, 2.20, 2.30, 2.40, 2.45, 2.60, 2.55, 2.50])
    # DEXTAUS twd_per_usd, v0 = 30.00 on 02-20 (1 day before; no anchor-day row).
    rows += _series(me, "DEXTAUS", "twd_per_usd", ("2020-02-20", 30.00),
                    [30.15, 30.30, 30.45, 30.60, 30.30, 30.06, 30.09, 29.94, 29.70, 29.55])
    # Ignored: DGS1 is not canonical; HY rows carry units "lin" (no class).
    rows += _series(me, "DGS1", "percent", ("2020-02-21", 1.50), [1.00] * 10)
    rows += _series(me, "BAMLH0A0HYM2", "lin", ("2020-02-21", 4.00), [5.00] * 10)
    return rows


def _covid_expected(me):
    m = me.move
    # oracle (hand-computed from the rows above, spec Definitions):
    # VIXCLS changes v-17: +8 +10.5 +10 +22 +19 | +16.5 +19.5 +14.5 +22.5 +21
    #   k5 end +19 peak +22 last 02-28; k10 end +21 peak +22.5 last 03-06.
    # DGS10 (v-1.50)x100: -10 -15 -20 -45 -40 | -35 -50 -55 -60 -75
    #   k5 end -40 peak -45; k10 end -75 peak -75.
    # DGS5 (v-1.40)x100: -5 -10 -8 -20 -15 -> k5 end -15 peak -20; k10: 7 rows < 10.
    # DEXJPUS (v/112-1)x100: -1 -1.5 -2 -3 -2.5 | -5 -4 -4.5 -6 -8.5
    #   k5 end -2.5 peak -3 last 03-02 (own dates); k10 end -8.5 peak -8.5 last 03-09.
    # BAA10Y (v-2.00)x100: +5 +10 +8 +20 +30 | +40 +45 +60 +55 +50
    #   k5 end +30 peak +30; k10 end +50 peak +60; no BAMLC0A0CM move -> IG_OAS.
    # DEXTAUS (v/30-1)x100: +0.5 +1 +1.5 +2 +1 | +0.2 +0.3 -0.2 -1 -1.5
    #   k5 end +1 peak +2 last 02-28; k10 end -1.5 peak +2 last 03-06.
    k5 = {
        "VIXCLS": m("VIX", "pts", 19.0, 22.0, "2020-02-28"),
        "DGS10": m("UST_10Y", "bp", -40.0, -45.0, "2020-02-28"),
        "DGS5": m("UST_5Y", "bp", -15.0, -20.0, "2020-02-28"),
        "DEXJPUS": m("USDJPY", "pct", -2.5, -3.0, "2020-03-02"),
        "BAA10Y": m("IG_OAS", "bp", 30.0, 30.0, "2020-02-28"),
        "DEXTAUS": m("USDTWD", "pct", 1.0, 2.0, "2020-02-28"),
    }
    k10 = {
        "VIXCLS": m("VIX", "pts", 21.0, 22.5, "2020-03-06"),
        "DGS10": m("UST_10Y", "bp", -75.0, -75.0, "2020-03-06"),
        "DEXJPUS": m("USDJPY", "pct", -8.5, -8.5, "2020-03-09"),
        "BAA10Y": m("IG_OAS", "bp", 50.0, 60.0, "2020-03-06"),
        "DEXTAUS": m("USDTWD", "pct", -1.5, 2.0, "2020-03-06"),
    }
    return k5, k10


@pytest.mark.criterion("C1")
def test_covid_case_all_classes_staleness_and_short_windows(me):
    # oracle: hand-computed changes on hand-written rows (see _covid_expected).
    k5, k10 = _covid_expected(me)
    rows = _covid_rows(me)
    me.assert_moves(episode_moves(rows, anchor=_ANCHOR, horizon=5), k5)
    me.assert_moves(episode_moves(rows, anchor=_ANCHOR, horizon=10), k10)


@pytest.mark.criterion("C1")
def test_baa_standin_only_where_ice_ig_has_no_move(me):
    # oracle: spec canonical table row 8, by hand. Anchor 2024-07-31; dates after:
    # 08-01 02 05 06 07 08 09 12 13 14.
    # BAMLC0A0CM v0 0.95: 1.00 1.05 1.10 1.12 1.08 1.20 -> +5 +10 +15 +17 +13 (+25):
    #   k5 end +13 peak +17 last 08-07; only 6 rows -> no move at k10.
    # BAA10Y v0 1.60 on 07-30 (1 day): +5 +10 +20 +15 +12 | +18 +30 +25 +22 +20:
    #   k5 end +12 peak +20, IG has a move -> field None;
    #   k10 end +20 peak +30, IG has none -> field IG_OAS.
    # BAMLH0A0HYM2 v0 3.20: +10 +25 +50 +40 +30 | +20 +15 +10 +5 +8:
    #   k5 end +30 peak +50 last 08-07; k10 end +8 peak +50 last 08-14.
    d = ["2024-08-01", "2024-08-02", "2024-08-05", "2024-08-06", "2024-08-07",
         "2024-08-08", "2024-08-09", "2024-08-12", "2024-08-13", "2024-08-14"]
    rows = (_series(me, "BAMLC0A0CM", "percent", ("2024-07-31", 0.95),
                    [1.00, 1.05, 1.10, 1.12, 1.08, 1.20], d)
            + _series(me, "BAA10Y", "percent", ("2024-07-30", 1.60),
                      [1.65, 1.70, 1.80, 1.75, 1.72, 1.78, 1.90, 1.85, 1.82, 1.80], d)
            + _series(me, "BAMLH0A0HYM2", "percent", ("2024-07-31", 3.20),
                      [3.30, 3.45, 3.70, 3.60, 3.50, 3.40, 3.35, 3.30, 3.25, 3.28], d))
    a = date(2024, 7, 31)
    m = me.move
    me.assert_moves(episode_moves(rows, anchor=a, horizon=5), {
        "BAMLC0A0CM": m("IG_OAS", "bp", 13.0, 17.0, "2024-08-07"),
        "BAA10Y": m(None, "bp", 12.0, 20.0, "2024-08-07"),
        "BAMLH0A0HYM2": m("HY_OAS", "bp", 30.0, 50.0, "2024-08-07"),
    })
    me.assert_moves(episode_moves(rows, anchor=a, horizon=10), {
        "BAA10Y": m("IG_OAS", "bp", 20.0, 30.0, "2024-08-14"),
        "BAMLH0A0HYM2": m("HY_OAS", "bp", 8.0, 50.0, "2024-08-14"),
    })


@pytest.mark.criterion("C1")
def test_ties_latest_anchor_row_and_seven_day_boundary(me):
    # oracle: by hand, anchor Thursday 2018-02-01; dates after 02-02 05 06 07 08.
    # VIXCLS: rows 01-31 30.00 and 02-01 20.00 -> v0 = 20.00 (the latest on/before).
    #   window 17.50 22.50 22.50 20.00 21.00 -> -2.5 +2.5 +2.5 0 +1:
    #   end +1.00; |-2.5| ties |+2.5| -> earliest, peak -2.50; last 02-08.
    # DGS30: v0 2.90 on 01-25 (7 days before: allowed): +5 -10 +8 +9 +3 ->
    #   end +3.0 peak -10.0.  THREEFYTP10 (field None): v0 0.50: +2 +5 +10 +8 +7 ->
    #   end +7.0 peak +10.0.  SOFR: latest row 01-24 (8 days) -> absent.
    # k = 10: every series has only 5 rows after the anchor -> {}.
    d = ["2018-02-02", "2018-02-05", "2018-02-06", "2018-02-07", "2018-02-08"]
    rows = (_series(me, "VIXCLS", "index_points",
                    [("2018-01-31", 30.00), ("2018-02-01", 20.00)],
                    [17.50, 22.50, 22.50, 20.00, 21.00], d)
            + _series(me, "DGS30", "percent", ("2018-01-25", 2.90),
                      [2.95, 2.80, 2.98, 2.99, 2.93], d)
            + _series(me, "THREEFYTP10", "percent", ("2018-02-01", 0.50),
                      [0.52, 0.55, 0.60, 0.58, 0.57], d)
            + _series(me, "SOFR", "percent", ("2018-01-24", 1.40),
                      [1.41, 1.42, 1.43, 1.44, 1.45], d))
    a = date(2018, 2, 1)
    m = me.move
    me.assert_moves(episode_moves(rows, anchor=a, horizon=5), {
        "VIXCLS": m("VIX", "pts", 1.0, -2.5, "2018-02-08"),
        "DGS30": m("UST_30Y", "bp", 3.0, -10.0, "2018-02-08"),
        "THREEFYTP10": m(None, "bp", 7.0, 10.0, "2018-02-08"),
    })
    assert episode_moves(rows, anchor=a, horizon=10) == {}


# ── properties ───────────────────────────────────────────────────────────────

_CLASSES = {
    # code: (units, field, unit, v0, value-from-step, change-from-step)
    "DGS10": ("percent", "UST_10Y", "bp", 4.00, lambda c: 4.00 + c / 100, lambda c: float(c)),
    "VIXCLS": ("index_points", "VIX", "pts", 20.0, lambda c: 20.0 + c / 4,
               lambda c: c / 4),
    "DEXJPUS": ("jpy_per_usd", "USDJPY", "pct", 100.0, lambda c: 100.0 + c / 100,
                lambda c: c / 100),
    # DFII10 removed by spec vocab-rates-decomp, which now pins its field
    # (REAL_YIELD_10Y); DGS10 still covers the percent -> bp class.
}
_A = date(2020, 3, 2)


@pytest.mark.criterion("C1")
@given(code=st.sampled_from(sorted(_CLASSES)),
       gap=st.integers(min_value=0, max_value=10),
       steps=st.lists(st.integers(min_value=-300, max_value=300).filter(bool),
                      max_size=14, unique_by=abs),
       k=st.integers(min_value=1, max_value=12))
def test_property_end_and_peak_closed_form(me, code, gap, steps, k):
    # oracle: closed form from the Definitions. Step i is a change c_i (distinct
    # |c_i|, so no ties): percent v = v0 + c/100 -> c bp; index v = v0 + c/4 -> c/4
    # pts; per_usd v = 100 + c/100 -> (v/100 - 1) x 100 = c/100 pct. The anchor row
    # sits `gap` days before the anchor (an older decoy row 3 days earlier); window
    # rows on consecutive days after it. Move iff gap <= 7 and len >= k: end = change
    # k-1, peak = the change of largest |c| among the first k, last = day k.
    units, fld, unit, v0, value, change = _CLASSES[code]
    rows = [me.row(code, (_A - timedelta(days=gap + 3)).isoformat(), 999.0, units),
            me.row(code, (_A - timedelta(days=gap)).isoformat(), v0, units)]
    rows += [me.row(code, (_A + timedelta(days=j + 1)).isoformat(), value(c), units)
             for j, c in enumerate(steps)]
    got = episode_moves(rows, anchor=_A, horizon=k)
    if gap > 7 or len(steps) < k:
        assert got == {}
        return
    win = steps[:k]
    peak = max(win, key=abs)
    me.assert_moves(got, {code: me.move(fld, unit, change(win[-1]), change(peak),
                                        (_A + timedelta(days=k)).isoformat())})


@pytest.mark.criterion("C1")
@given(steps=st.lists(st.integers(min_value=-8, max_value=8), min_size=5, max_size=5))
def test_property_ties_go_to_the_earliest(me, steps):
    # oracle: index_points changes are exact quarter multiples (v = 20 + c/4), so
    # ties are exact; the peak is the first window change whose |c| is the maximum.
    rows = [me.row("VIXCLS", _A.isoformat(), 20.0, "index_points")]
    rows += [me.row("VIXCLS", (_A + timedelta(days=j + 1)).isoformat(), 20.0 + c / 4,
                    "index_points") for j, c in enumerate(steps)]
    top = max(abs(c) for c in steps)
    first = next(c for c in steps if abs(c) == top)
    me.assert_moves(episode_moves(rows, anchor=_A, horizon=5), {
        "VIXCLS": me.move("VIX", "pts", steps[-1] / 4, first / 4,
                          (_A + timedelta(days=5)).isoformat())})


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(data=st.data())
def test_property_row_order_and_irrelevant_rows_change_nothing(me, data):
    # oracle: the hand-computed COVID expectation (_covid_expected). The Definitions
    # use date order and each series' own rows, so permuting the rows and adding
    # rows of non-canonical series, unknown units, or older pre-anchor rows (a later
    # pre-anchor row exists) leaves both horizons unchanged.
    extra = [me.row("DGS3MO", "2020-02-24", 1.0, "percent"),
             me.row("VIXCLS", "2020-02-25", 80.0, "lin"),
             me.row("DGS10", "2020-01-02", 1.90, "percent"),
             me.row("XYZ", "2020-02-24", 1.0, "eur_per_usd")]
    rows = data.draw(st.permutations(_covid_rows(me) + extra))
    k5, k10 = _covid_expected(me)
    me.assert_moves(episode_moves(rows, anchor=_ANCHOR, horizon=5), k5)
    me.assert_moves(episode_moves(rows, anchor=_ANCHOR, horizon=10), k10)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(ig_rows=st.integers(min_value=0, max_value=12),
       steps=st.lists(st.integers(min_value=1, max_value=200), min_size=10, max_size=10,
                      unique=True),
       k=st.sampled_from([5, 10]))
def test_property_baa_field_depends_only_on_ice_ig_move(me, ig_rows, steps, k):
    # oracle: canonical table row 8. BAA10Y (v0 = 3.00, v = 3 + c/100 -> +c bp) is
    # IG_OAS iff BAMLC0A0CM has no move at k, i.e. iff it has fewer than k window rows
    # (its anchor row is fresh); otherwise its field is None. end/peak never change.
    rows = [me.row("BAA10Y", _A.isoformat(), 3.00, "percent")]
    rows += [me.row("BAA10Y", (_A + timedelta(days=j + 1)).isoformat(), 3.00 + c / 100,
                    "percent") for j, c in enumerate(steps)]
    rows += [me.row("BAMLC0A0CM", _A.isoformat(), 1.00, "percent")]
    rows += [me.row("BAMLC0A0CM", (_A + timedelta(days=j + 1)).isoformat(), 1.50,
                    "percent") for j in range(ig_rows)]
    got = episode_moves(rows, anchor=_A, horizon=k)
    win = steps[:k]
    baa = me.move("IG_OAS" if ig_rows < k else None, "bp", float(win[-1]),
                  float(max(win)), (_A + timedelta(days=k)).isoformat())
    expected = {"BAA10Y": baa}
    if ig_rows >= k:
        expected["BAMLC0A0CM"] = me.move("IG_OAS", "bp", 50.0, 50.0,
                                         (_A + timedelta(days=k)).isoformat())
    me.assert_moves(got, expected)
