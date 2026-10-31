"""C2: a minutes item is built from the linked minutes page."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.mark.criterion("C2")
def test_minutes_document_from_the_minutes_page(fm):
    # oracle: C2 by hand. Requests: release, then minutes. The document takes the
    # minutes page's URL and hash, the release URL's date (2026-08-19), and doc_id
    # press_monetary_2026-08-19_fomcminutes20260729.htm. Chunks hold the minutes text,
    # not the notice, and each of the four headings is some chunk's section.
    client = fm.FakeFedClient({fm.RELEASE_URL: fm.RELEASE_HTML, fm.MINUTES_URL: fm.MINUTES_HTML})
    docs = fm.handler(client).fetch(fm.minutes_item())
    assert client.requests == [fm.RELEASE_URL, fm.MINUTES_URL]
    assert len(docs) == 1
    d = docs[0]
    assert d.url == fm.MINUTES_URL
    assert d.content_hash == hashlib.sha256(fm.MINUTES_HTML).hexdigest()
    assert d.doc_id == "press_monetary_2026-08-19_fomcminutes20260729.htm"
    assert d.doc_type == "fomc_minutes"
    assert d.publication == "FOMC Minutes"
    assert d.release_date == date(2026, 8, 19)
    text = "\n".join(c.text for c in d.chunks)
    for body in fm.SECTIONS.values():
        assert body in " ".join(text.split()), body[:40]
    assert fm.NOTICE not in text
    assert set(fm.SECTIONS) <= {c.metadata.section for c in d.chunks}


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=15, deadline=None)
@given(day=st.integers(min_value=1, max_value=28), month=st.integers(min_value=1, max_value=12))
def test_property_release_date_and_ids_follow_the_urls(fm, day, month):
    # oracle: C2. Relation: moving the release URL's date moves release_date and the
    # doc_id prefix with it; the minutes URL (and so url, hash, file name) is unchanged.
    rel = date(2026, month, day)
    release_url = f"{fm.BASE}/newsevents/pressreleases/monetary{rel:%Y%m%d}a.htm"
    client = fm.FakeFedClient({release_url: fm.RELEASE_HTML, fm.MINUTES_URL: fm.MINUTES_HTML})
    docs = fm.handler(client).fetch(fm.minutes_item(release_url))
    assert len(docs) == 1
    d = docs[0]
    assert d.release_date == rel
    assert d.doc_id == f"press_monetary_{rel.isoformat()}_fomcminutes20260729.htm"
    assert d.url == fm.MINUTES_URL
    assert d.content_hash == hashlib.sha256(fm.MINUTES_HTML).hexdigest()
