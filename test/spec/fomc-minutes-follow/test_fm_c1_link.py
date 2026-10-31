"""C1: minutes_page_url finds the HTML minutes link on a release page."""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ingest.fed.handlers.press_monetary import minutes_page_url


@pytest.mark.criterion("C1")
def test_release_page_link_resolved_and_pdf_ignored(fm):
    # oracle: C1 by hand. The release page links the PDF first and the HTML minutes
    # second; the HTML one is returned, resolved against the release URL.
    assert minutes_page_url(fm.RELEASE_HTML, fm.RELEASE_URL) == fm.MINUTES_URL


@pytest.mark.criterion("C1")
def test_no_link_and_absolute_link(fm):
    # oracle: C1 by hand. A page with no minutes link gives None; an absolute link on
    # another page is returned as is; the first of two HTML links wins; a str page works.
    assert minutes_page_url(fm.STATEMENT_HTML, fm.STATEMENT_URL) is None
    page = ('<div id="article"><a href="https://www.federalreserve.gov/monetarypolicy/'
            'fomcminutes20260318.htm">x</a><a href="/monetarypolicy/fomcminutes20260128.htm">'
            'y</a></div>')
    assert minutes_page_url(page, fm.RELEASE_URL) == \
        "https://www.federalreserve.gov/monetarypolicy/fomcminutes20260318.htm"
    assert minutes_page_url(fm.RELEASE_HTML.decode(), fm.RELEASE_URL) == fm.MINUTES_URL


@pytest.mark.criterion("C1")
@settings(max_examples=40, deadline=None)
@given(day=st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)),
       decoys=st.lists(st.sampled_from([
           "/monetarypolicy/files/fomcminutes{d}.pdf", "/monetarypolicy/fomccalendars.htm",
           "/newsevents/pressreleases/monetary{d}a.htm", "/monetarypolicy/fomcminutes.htm"]),
           max_size=4))
def test_property_first_html_minutes_link_after_decoys(fm, day, decoys):
    # oracle: C1 computed in the test: decoy links (PDF, calendar, release, undated)
    # never match; the dated HTML minutes link placed after them is returned, absolute.
    d = day.strftime("%Y%m%d")
    links = "".join(f'<a href="{x.format(d=d)}">x</a>' for x in decoys)
    page = f'<div id="article">{links}<a href="/monetarypolicy/fomcminutes{d}.htm">m</a></div>'
    assert minutes_page_url(page, fm.RELEASE_URL) == \
        f"{fm.BASE}/monetarypolicy/fomcminutes{d}.htm"
