"""C3: no notice-only minutes document; other doc types unchanged."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.mark.criterion("C3")
def test_missing_link_or_failed_fetch_stores_nothing(fm):
    # oracle: C3 by hand. A release page without a minutes link gives [] after one
    # request; a minutes URL answering 404 gives [] after two. In a third case the
    # minutes page is served, so a do-nothing fallback (storing the notice) fails.
    no_link = fm.FakeFedClient({fm.RELEASE_URL: fm.STATEMENT_HTML})
    assert fm.handler(no_link).fetch(fm.minutes_item()) == []
    assert no_link.requests == [fm.RELEASE_URL]

    missing = fm.FakeFedClient({fm.RELEASE_URL: fm.RELEASE_HTML})
    assert fm.handler(missing).fetch(fm.minutes_item()) == []
    assert missing.requests == [fm.RELEASE_URL, fm.MINUTES_URL]

    ok = fm.FakeFedClient({fm.RELEASE_URL: fm.RELEASE_HTML, fm.MINUTES_URL: fm.MINUTES_HTML})
    docs = fm.handler(ok).fetch(fm.minutes_item())
    assert [d.url for d in docs] == [fm.MINUTES_URL]


@pytest.mark.criterion("C3")
def test_statement_makes_one_request_and_keeps_its_url(fm):
    # oracle: C3 — an fomc_statement item fetches only its release URL and its document
    # keeps that URL, even when the page happens to link a minutes page. The minutes
    # case alongside it must follow the link, so a do-nothing handler fails.
    page = fm.STATEMENT_HTML.replace(
        b"</div>", b'<a href="/monetarypolicy/fomcminutes20260729.htm">m</a></div>')
    client = fm.FakeFedClient({fm.STATEMENT_URL: page, fm.MINUTES_URL: fm.MINUTES_HTML,
                               fm.RELEASE_URL: fm.RELEASE_HTML})
    h = fm.handler(client)
    docs = h.fetch(fm.statement_item())
    assert client.requests == [fm.STATEMENT_URL]
    assert [d.url for d in docs] == [fm.STATEMENT_URL]
    assert docs[0].doc_type == "fomc_statement"
    minutes = h.fetch(fm.minutes_item())
    assert [d.url for d in minutes] == [fm.MINUTES_URL]


@pytest.mark.criterion("C3")
@settings(max_examples=10, deadline=None)
@given(status_missing=st.booleans())
def test_property_never_a_notice_document(fm, status_missing):
    # oracle: C3 — whether the minutes link is absent or broken, no document carrying
    # the notice text is returned; with both pages present, exactly one document is,
    # built from the minutes page.
    pages = {fm.RELEASE_URL: fm.STATEMENT_HTML if status_missing else fm.RELEASE_HTML}
    assert fm.handler(fm.FakeFedClient(pages)).fetch(fm.minutes_item()) == []
    both = fm.FakeFedClient({fm.RELEASE_URL: fm.RELEASE_HTML, fm.MINUTES_URL: fm.MINUTES_HTML})
    docs = fm.handler(both).fetch(fm.minutes_item())
    assert len(docs) == 1 and all(fm.NOTICE not in c.text for c in docs[0].chunks)
