"""C2: FredDailyHandler requests each series once and emits one doc per partition."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

AAA = [("2025-01-02", "1.5"), ("2025-06-30", "."), ("2025-12-31", "2.25"),
       ("2026-01-02", "3.0"), ("2026-10-13", "4.125")]
BBB = [("2025-03-15", "10.5"), ("2026-02-02", "11.25")]


def _assert_doc(fred, doc, *, series, year, release, rows):
    assert doc.doc_id == f"fred_daily_{series.lower()}_{year}"
    assert doc.doc_type == "fred_daily_series"
    assert doc.url == f"fred://{series}/{year}"
    assert doc.release_date == release
    assert doc.chunks == []
    fred.assert_rows(doc.timeseries, rows)


@pytest.mark.criterion("C2")
def test_routine_run_requests_and_documents(fred):
    # oracle: hand-written bodies AAA/BBB mapped by hand. today=2026-10-14, routine
    # run -> observation_start "2025-01-01" (D.year-1). AAA 2025: 01-02 and 12-31
    # ("." skipped), release 2025-12-31; AAA 2026: 01-02 and 10-13. BBB 2025 starts
    # late but Y = D.year-1 is not an old partition (never window-cut), so it is
    # emitted.
    client = fred.FakeFredClient({"AAA": AAA, "BBB": BBB})
    cfg = fred.endpoint([("AAA", "percent"), ("BBB", "index_points")])
    docs, raw, client = fred.run(cfg, client, refresh=False)

    assert len(client.requests) == 2
    by_series = {r["params"].get("series_id"): r["params"] for r in client.requests}
    assert set(by_series) == {"AAA", "BBB"}
    for params in by_series.values():
        assert params.get("file_type") == "json"
        assert params.get("api_key") == fred.SENTINEL
        assert params.get("observation_start") == "2025-01-01"

    got = fred.by_id(docs)
    assert set(got) == {"fred_daily_aaa_2025", "fred_daily_aaa_2026",
                        "fred_daily_bbb_2025", "fred_daily_bbb_2026"}
    _assert_doc(fred, got["fred_daily_aaa_2025"], series="AAA", year=2025,
                release=date(2025, 12, 31),
                rows=[fred.row("AAA", "2025-01-02", 1.5, "percent"),
                      fred.row("AAA", "2025-12-31", 2.25, "percent")])
    _assert_doc(fred, got["fred_daily_aaa_2026"], series="AAA", year=2026,
                release=date(2026, 10, 13),
                rows=[fred.row("AAA", "2026-01-02", 3.0, "percent"),
                      fred.row("AAA", "2026-10-13", 4.125, "percent")])
    _assert_doc(fred, got["fred_daily_bbb_2025"], series="BBB", year=2025,
                release=date(2025, 3, 15),
                rows=[fred.row("BBB", "2025-03-15", 10.5, "index_points")])
    _assert_doc(fred, got["fred_daily_bbb_2026"], series="BBB", year=2026,
                release=date(2026, 2, 2),
                rows=[fred.row("BBB", "2026-02-02", 11.25, "index_points")])
    fred.assert_no_key(docs, raw)


@pytest.mark.criterion("C2")
def test_backfill_run_uses_backfill_start_and_skips_window_cut(fred):
    # oracle: hand-mapped. Backfill run -> observation_start = content.backfill_start
    # ("2010-01-01" here, to show it comes from config) for every series; the fake
    # server drops rows before it, so CCC's 2009-12-31 is not in the response.
    # Old partitions are Y < 2025. Backfill-start year 2010: CCC starts exactly
    # 01-10 (on the boundary -> kept); DDD starts 01-11 (after -> window-cut,
    # skipped). Later old years are kept iff the same response has an earlier row:
    # CCC 2011, 2012 (starts 01-11, a gap, not a cut), 2023 (starts 10-16) and 2024
    # all have earlier rows -> kept; DDD 2011 has DDD 2010's rows before it -> kept;
    # EEE's data begins 2016-06-01 with nothing earlier -> 2016 skipped, 2017 kept.
    # 2025 and 2026 are not old -> kept.
    ccc = [("2009-12-31", "9.0"),          # before backfill_start: server drops it
           ("2010-01-10", "1.0"), ("2010-12-31", "1.5"),
           ("2011-01-10", "2.0"),
           ("2012-01-11", "3.0"), ("2012-12-31", "3.5"),
           ("2023-10-16", "4.0"), ("2023-12-29", "4.5"),
           ("2024-01-02", "5.0"),
           ("2025-05-01", "6.0"),
           ("2026-01-02", "7.0")]
    ddd = [("2010-01-11", "20.0"), ("2010-06-30", "20.5"),
           ("2011-03-01", "21.0")]
    eee = [("2016-06-01", "30.0"), ("2016-12-30", "30.5"),
           ("2017-01-03", "31.0")]
    client = fred.FakeFredClient({"CCC": ccc, "DDD": ddd, "EEE": eee})
    cfg = fred.endpoint([("CCC", "percent"), ("DDD", "percent"),
                         ("EEE", "index_points")], backfill_start="2010-01-01")
    docs, raw, client = fred.run(cfg, client, refresh=True)

    assert len(client.requests) == 3
    by_series = {r["params"].get("series_id"): r["params"] for r in client.requests}
    assert set(by_series) == {"CCC", "DDD", "EEE"}
    for params in by_series.values():
        assert params.get("file_type") == "json"
        assert params.get("api_key") == fred.SENTINEL
        assert params.get("observation_start") == "2010-01-01"

    got = fred.by_id(docs)
    assert set(got) == {"fred_daily_ccc_2010", "fred_daily_ccc_2011",
                        "fred_daily_ccc_2012", "fred_daily_ccc_2023",
                        "fred_daily_ccc_2024", "fred_daily_ccc_2025",
                        "fred_daily_ccc_2026", "fred_daily_ddd_2011",
                        "fred_daily_eee_2017"}
    _assert_doc(fred, got["fred_daily_ccc_2010"], series="CCC", year=2010,
                release=date(2010, 12, 31),
                rows=[fred.row("CCC", "2010-01-10", 1.0, "percent"),
                      fred.row("CCC", "2010-12-31", 1.5, "percent")])
    _assert_doc(fred, got["fred_daily_ccc_2011"], series="CCC", year=2011,
                release=date(2011, 1, 10),
                rows=[fred.row("CCC", "2011-01-10", 2.0, "percent")])
    _assert_doc(fred, got["fred_daily_ccc_2012"], series="CCC", year=2012,
                release=date(2012, 12, 31),
                rows=[fred.row("CCC", "2012-01-11", 3.0, "percent"),
                      fred.row("CCC", "2012-12-31", 3.5, "percent")])
    _assert_doc(fred, got["fred_daily_ccc_2023"], series="CCC", year=2023,
                release=date(2023, 12, 29),
                rows=[fred.row("CCC", "2023-10-16", 4.0, "percent"),
                      fred.row("CCC", "2023-12-29", 4.5, "percent")])
    _assert_doc(fred, got["fred_daily_ccc_2024"], series="CCC", year=2024,
                release=date(2024, 1, 2),
                rows=[fred.row("CCC", "2024-01-02", 5.0, "percent")])
    _assert_doc(fred, got["fred_daily_ccc_2025"], series="CCC", year=2025,
                release=date(2025, 5, 1),
                rows=[fred.row("CCC", "2025-05-01", 6.0, "percent")])
    _assert_doc(fred, got["fred_daily_ccc_2026"], series="CCC", year=2026,
                release=date(2026, 1, 2),
                rows=[fred.row("CCC", "2026-01-02", 7.0, "percent")])
    _assert_doc(fred, got["fred_daily_ddd_2011"], series="DDD", year=2011,
                release=date(2011, 3, 1),
                rows=[fred.row("DDD", "2011-03-01", 21.0, "percent")])
    _assert_doc(fred, got["fred_daily_eee_2017"], series="EEE", year=2017,
                release=date(2017, 1, 3),
                rows=[fred.row("EEE", "2017-01-03", 31.0, "index_points")])
    fred.assert_no_key(docs, raw)


@pytest.mark.criterion("C2")
def test_failing_series_emit_nothing_and_others_still_run(fred):
    # oracle: spec C2 Failures — BAD1 fails with a non-200 (HTTP 400), BAD2's body
    # is not JSON, BAD3 has no observations list: all three emit nothing. OK1 and
    # OK2 still emit their single 2026 partition each.
    client = fred.FakeFredClient({
        "BAD1": fred.FailStatus(400),
        "BAD2": b"<html>upstream error</html>",
        "BAD3": b'{"error_code": 500, "error_message": "oops"}',
        "OK1": [("2026-03-02", "0.5")],
        "OK2": [("2026-04-01", "-1.5")],
    })
    cfg = fred.endpoint([("BAD1", "percent"), ("BAD2", "percent"),
                         ("BAD3", "percent"), ("OK1", "percent"),
                         ("OK2", "jpy_per_usd")])
    docs, raw, client = fred.run(cfg, client, refresh=False)

    requested = [r["params"].get("series_id") for r in client.requests]
    assert sorted(requested) == ["BAD1", "BAD2", "BAD3", "OK1", "OK2"]
    got = fred.by_id(docs)
    assert set(got) == {"fred_daily_ok1_2026", "fred_daily_ok2_2026"}
    fred.assert_rows(got["fred_daily_ok1_2026"].timeseries,
                     [fred.row("OK1", "2026-03-02", 0.5, "percent")])
    fred.assert_rows(got["fred_daily_ok2_2026"].timeseries,
                     [fred.row("OK2", "2026-04-01", -1.5, "jpy_per_usd")])
    fred.assert_no_key(docs, raw)


_DAY0 = date(2025, 1, 1)


@pytest.mark.criterion("C2")
@given(
    offsets=st.sets(st.integers(min_value=0, max_value=650), min_size=1, max_size=40),
    cents=st.lists(st.integers(min_value=-5000, max_value=50000), min_size=40,
                   max_size=40),
    refresh=st.booleans(),
)
def test_property_partitions_cover_every_row_once(fred, offsets, cents, refresh):
    # oracle: closed form from the spec's Partition definition. All dates fall in
    # 2025..2026 (Y >= D.year-1, never window-cut), so the documents are exactly one
    # per year present, each holding that year's rows in date order, with
    # release_date = that year's latest date; the key appears nowhere.
    days = sorted(_DAY0 + timedelta(days=o) for o in offsets)
    obs = [(d.isoformat(), f"{c / 100:.2f}") for d, c in zip(days, cents, strict=False)]
    client = fred.FakeFredClient({"DGS5": obs})
    cfg = fred.endpoint([("DGS5", "percent")])
    docs, raw, client = fred.run(cfg, client, refresh=refresh)

    expected: dict[int, list] = {}
    for d, c in zip(days, cents, strict=False):
        expected.setdefault(d.year, []).append(
            fred.row("DGS5", d.isoformat(), c / 100, "percent"))
    got = fred.by_id(docs)
    assert set(got) == {f"fred_daily_dgs5_{y}" for y in expected}
    for y, rows in expected.items():
        doc = got[f"fred_daily_dgs5_{y}"]
        assert doc.url == f"fred://DGS5/{y}"
        assert doc.doc_type == "fred_daily_series"
        assert doc.release_date == date.fromisoformat(rows[-1]["date"])
        assert doc.chunks == []
        fred.assert_rows(doc.timeseries, rows)
    fred.assert_no_key(docs, raw)
