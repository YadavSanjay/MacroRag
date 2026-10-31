"""C1: parse_acm_daily reads the requested series from an ACM daily sheet grid."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ingest.usstats.parsers import parse_acm_daily


@pytest.mark.criterion("C1")
def test_rows_per_series_in_order_with_skips(acm):
    # oracle: C1 by hand. A title row precedes the DATE header. Dates are strings,
    # date and datetime values; rows are returned per requested series (ACMTP10 then
    # ACMY10), each in date order. Skipped: a "n/a" value, an empty value, an inf, a
    # row with an unparseable date ("Total").
    grid = [
        ["Adrian, Crump and Moench term premium estimates"],
        ["DATE", "ACMY10", "ACMTP05", "ACMTP10"],
        ["15-Oct-2026", 5.20, 0.10, 0.80],
        ["13-Oct-2026", 5.19, 0.11, 0.81],
        [date(2026, 10, 14), 5.17, 0.12, ""],
        [datetime(2026, 10, 12), "n/a", 0.13, 0.79],
        ["Total", 1.0, 1.0, 1.0],
        ["16-Oct-2026", float("inf"), 0.14, 0.82],
    ]
    got = parse_acm_daily(grid, ["ACMTP10", "ACMY10"])
    assert got == [
        acm.acm_row("ACMTP10", "2026-10-12", 0.79),
        acm.acm_row("ACMTP10", "2026-10-13", 0.81),
        acm.acm_row("ACMTP10", "2026-10-15", 0.80),
        acm.acm_row("ACMTP10", "2026-10-16", 0.82),
        acm.acm_row("ACMY10", "2026-10-13", 5.19),
        acm.acm_row("ACMY10", "2026-10-14", 5.17),
        acm.acm_row("ACMY10", "2026-10-15", 5.20),
    ]


@pytest.mark.criterion("C1")
def test_missing_header_or_column_raises(acm):
    # oracle: C1 — no DATE header, or a requested column absent, raises ValueError; a
    # valid grid in the same test must parse, so a stub raising always fails.
    with pytest.raises(ValueError):
        parse_acm_daily([["x", "y"], ["1-Jan-2026", 1.0]], ["ACMTP10"])
    with pytest.raises(ValueError):
        parse_acm_daily([["DATE", "ACMY10"], ["01-Jan-2026", 1.0]], ["ACMTP10"])
    assert parse_acm_daily([["DATE", "ACMTP10"], ["02-Jan-2026", 0.5]], ["ACMTP10"]) == \
        [acm.acm_row("ACMTP10", "2026-01-02", 0.5)]


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None)
@given(days=st.lists(st.dates(min_value=date(2005, 1, 1), max_value=date(2026, 12, 31)),
                     unique=True, min_size=1, max_size=30),
       vals=st.lists(st.floats(min_value=-3, max_value=5, allow_nan=False), min_size=30,
                     max_size=30),
       as_str=st.booleans())
def test_property_any_order_any_date_form(acm, days, vals, as_str):
    # oracle: C1 computed in the test: whatever order the rows come in and whichever date
    # form is used, the result is the rows sorted by date with ISO dates and the value
    # unchanged (rel 1e-12: string-free float passthrough).
    vals = vals[:len(days)]
    grid = [["DATE", "ACMTP10"]]
    for d, v in zip(days, vals, strict=True):
        grid.append([d.strftime("%d-%b-%Y") if as_str else d, v])
    got = parse_acm_daily(grid, ["ACMTP10"])
    want = sorted(((d.isoformat(), v) for d, v in zip(days, vals, strict=True)), key=lambda t: t[0])
    assert [r["date"] for r in got] == [d for d, _ in want]
    assert [r["value"] for r in got] == pytest.approx([v for _, v in want], rel=1e-12)
    assert all(r["series_code"] == "ACMTP10" and r["units"] == "percent"
               and r["source"] == "NYFED_ACM" for r in got)
