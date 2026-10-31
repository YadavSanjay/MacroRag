"""C2: AcmDailyHandler downloads the workbook and emits per-year numeric documents."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

ROWS = [  # DATE, ACMY10, ACMTP05, ACMTP10
    ["30-Dec-2004", 4.2, 0.4, 0.90],
    ["03-Jan-2005", 4.3, 0.4, 0.95],
    ["29-Dec-2023", 3.9, 0.1, 0.20],
    ["02-Jan-2025", 4.6, 0.2, 0.55],
    ["31-Dec-2025", 4.5, 0.2, 0.60],
    ["02-Jan-2026", 4.6, 0.3, 0.62],
    ["14-Oct-2026", 5.2, 0.3, 0.81],
]


@pytest.mark.criterion("C2")
def test_routine_run_emits_current_and_previous_year(acm):
    # oracle: C2 by hand. Routine run, D = 2026-10-15: start 2025-01-01, so ACMTP10 rows of
    # 2025 and 2026 only, one document each; one GET with use_cache=False.
    client = acm.FakeClient({acm.URL: acm.workbook(ROWS)})
    items, docs = acm.run(acm.endpoint(), client, refresh=False)
    assert len(items) == 1 and items[0].url == acm.URL
    assert [(u, k.get("use_cache")) for u, k in client.requests] == [(acm.URL, False)]
    by = {d.doc_id: d for d in docs}
    assert sorted(by) == ["acm_daily_acmtp10_2025", "acm_daily_acmtp10_2026"]
    d25, d26 = by["acm_daily_acmtp10_2025"], by["acm_daily_acmtp10_2026"]
    assert d25.timeseries == [acm.acm_row("ACMTP10", "2025-01-02", 0.55),
                              acm.acm_row("ACMTP10", "2025-12-31", 0.60)]
    assert d26.timeseries == [acm.acm_row("ACMTP10", "2026-01-02", 0.62),
                              acm.acm_row("ACMTP10", "2026-10-14", 0.81)]
    for d, y, last in ((d25, 2025, date(2025, 12, 31)), (d26, 2026, date(2026, 10, 14))):
        assert d.doc_type == "acm_daily_series"
        assert d.url == f"acm://ACMTP10/{y}"
        assert d.release_date == last
        assert d.chunks == []


@pytest.mark.criterion("C2")
def test_backfill_from_backfill_start_and_failures_emit_nothing(acm):
    # oracle: C2 by hand. Backfill (refresh): start 2005-01-01, so 2005, 2023, 2025, 2026
    # (the 2004 row is before the start). A 500 response, a missing sheet, and a missing
    # series column each emit nothing.
    client = acm.FakeClient({acm.URL: acm.workbook(ROWS)})
    _, docs = acm.run(acm.endpoint(), client, refresh=True)
    assert sorted(d.doc_id for d in docs) == [
        "acm_daily_acmtp10_2005", "acm_daily_acmtp10_2023", "acm_daily_acmtp10_2025",
        "acm_daily_acmtp10_2026"]
    assert {d.doc_id: len(d.timeseries) for d in docs}["acm_daily_acmtp10_2005"] == 1
    assert acm.run(acm.endpoint(), acm.FakeClient({acm.URL: acm.workbook(ROWS)}, status=500),
                   refresh=False)[1] == []
    assert acm.run(acm.endpoint(sheet="ACM Weekly"),
                   acm.FakeClient({acm.URL: acm.workbook(ROWS)}), refresh=False)[1] == []
    assert acm.run(acm.endpoint(series=["ACMTP99"]),
                   acm.FakeClient({acm.URL: acm.workbook(ROWS)}), refresh=False)[1] == []


@pytest.mark.criterion("C2")
@settings(max_examples=12, deadline=None)
@given(year=st.integers(min_value=2015, max_value=2030))
def test_property_routine_window_follows_today(acm, year):
    # oracle: C2 computed in the test. With D = <year>-06-30 and one row on 15 March of
    # every year 2005..2031, a routine run emits exactly the years from D.year-1 on (the
    # start bound is the only bound; the file holds no rows after its own date).
    rows = [[f"15-Mar-{y}", 4.0, 0.2, 0.5 + (y - 2005) / 100] for y in range(2005, 2032)]
    client = acm.FakeClient({acm.URL: acm.workbook(rows)})
    _, docs = acm.run(acm.endpoint(), client, refresh=False, today=date(year, 6, 30))
    assert sorted(d.doc_id for d in docs) == [f"acm_daily_acmtp10_{y}"
                                              for y in range(year - 1, 2032)]
