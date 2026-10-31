"""Unit tests for best-effort publication-date extraction (offline)."""

from __future__ import annotations

from datetime import date

from ingest.common.dateextract import (
    extract_date_from_html,
    extract_date_from_text,
)


def test_text_month_day_year():
    assert extract_date_from_text("Eye on the Market — January 15, 2026\nBody...") \
        == date(2026, 1, 15)
    assert extract_date_from_text("Published Jan. 3, 2025 by the desk") == date(2025, 1, 3)


def test_text_day_month_year_and_iso():
    assert extract_date_from_text("Note dated 5 March 2024.") == date(2024, 3, 5)
    assert extract_date_from_text("release: 2026-04-22 (final)") == date(2026, 4, 22)


def test_text_picks_dateline_over_body_references():
    body = ("March 5, 2026\n\nThis piece revisits the 2008 crisis and the "
            "January 2020 selloff to frame the outlook.")
    assert extract_date_from_text(body) == date(2026, 3, 5)


def test_text_none_when_absent_or_implausible():
    assert extract_date_from_text("no date here at all") is None
    assert extract_date_from_text("") is None
    # implausible year is rejected
    assert extract_date_from_text("dated 1899-01-01") is None


def test_html_meta_published_time():
    html = (b'<html><head>'
            b'<meta property="article:published_time" content="2026-05-11T09:00:00Z">'
            b'</head><body><p>Weekly commentary body.</p></body></html>')
    assert extract_date_from_html(html) == date(2026, 5, 11)


def test_html_time_tag_then_visible_dateline():
    html_time = b'<html><body><time datetime="2025-11-02">Nov 2</time><p>x</p></body></html>'
    assert extract_date_from_html(html_time) == date(2025, 11, 2)

    html_visible = b'<html><body><p>Posted February 9, 2026</p><p>body</p></body></html>'
    assert extract_date_from_html(html_visible) == date(2026, 2, 9)


def test_html_none_when_absent():
    assert extract_date_from_html(b"<html><body><p>no dates</p></body></html>") is None


def test_max_date_rejects_future_and_picks_earlier_valid():
    # a chart/projection date in the future is skipped; an earlier real date wins
    body = ("Presented May 4, 2026.\n\nProjections extend to December 31, 2026 "
            "and beyond under the baseline path.")
    assert extract_date_from_text(body, max_date=date(2026, 7, 8)) == date(2026, 5, 4)
    # if the ONLY date is in the future, return None rather than a bad date
    assert extract_date_from_text("outlook through 2026-12-31",
                                  max_date=date(2026, 7, 8)) is None
    # a future meta date is never returned as-is (guarantee: result <= max_date)
    future_meta = b'<html><head><meta name="date" content="2026-12-31"></head><body/></html>'
    d = extract_date_from_html(future_meta, max_date=date(2026, 7, 8))
    assert d is None or d <= date(2026, 7, 8)
    assert d != date(2026, 12, 31)
