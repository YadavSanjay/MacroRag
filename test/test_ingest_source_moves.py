"""Sources that moved or changed shape on the publisher side (2026-10 refresh).

Offline (fake HTTP client), driven by the real tier configs so a URL pattern
regression in ``configs/ingest/`` fails here too:
  * BIS QR + AER moved from ``publ/qtrpdf|arpdf`` to ``publications/qr-|aer-``;
  * TSMC press releases moved to ``pr.tsmc.com`` with no date metadata;
  * SLOOS is discovered from its landing page (the RSS feed is DDP notices only)
    and dated from the page footer, not the survey month in the URL;
  * SLOOS and TSMC chunks carry the release text only, not site chrome;
  * morganstanley.com dates its pages only via ``content_publishedAt``;
  * ``query --no-llm`` prints candidates that never got a rerank score;
  * ingest logging must not emit request URLs (they carry API keys).
"""

from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace

from ingest.chips.handlers.tsmc import TsmcHandler
from ingest.common import dateextract, logging_utils
from ingest.common.chunking import Chunker
from ingest.common.config import ChunkingSettings, load_tier_config
from ingest.common.http_client import sha256_hex
from ingest.common.models import FetchResult
from ingest.fed.handlers.sloos import SloosHandler
from ingest.fed.parsers import date_from_last_update
from ingest.intl.handlers.bis_quarterly import BisQuarterlyHandler

TODAY = date(2026, 10, 6)


class _FakeClient:
    """Serves fixed bytes per URL; HEAD is 200 only for ``heads``."""

    def __init__(self, pages: dict[str, bytes], heads: set[str] = frozenset()):
        self._pages = pages
        self._heads = heads

    def get(self, url, use_cache=True):
        content = self._pages.get(url, b"")
        return FetchResult(url=url, status_code=200 if content else 404,
                           content=content, content_type="text/html",
                           content_hash=sha256_hex(content))

    def head(self, url):
        return 200 if url in self._heads else 404


class _FakeCache:
    def is_processed(self, url, content_hash):
        return False


class _FakeRaw:
    def save(self, *a, **k):
        return "raw/path"


def _ctx(tier: int, client: _FakeClient):
    return SimpleNamespace(
        tier=SimpleNamespace(tier=tier, publisher="Test", licence="public"),
        client=client, cache=_FakeCache(), raw_store=_FakeRaw(),
        chunker=Chunker(ChunkingSettings()), today=TODAY,
        settings=SimpleNamespace(http=SimpleNamespace(user_agent="test")),
        refresh=False,
    )


def _endpoint(tier_name: str, endpoint_id: str):
    return next(ep for ep in load_tier_config(tier_name).endpoints if ep.id == endpoint_id)


# ── BIS ──────────────────────────────────────────────────────────────────────

_BIS_ISSUE = (
    b'<html><body>'
    b'<a href="https://www.bis.org/publications/qr-202609.pdf">Full issue</a>'
    b'<a href="/publications/qr-202609/markets-take-stock">Markets take stock</a>'
    b'<a href="/publications/qr-202609/no-pdf-yet">No PDF yet</a>'
    b'<a href="/publications/qr-202603/older-article">Older issue</a>'
    b'</body></html>'
)
_BIS_ARTICLE = (
    b'<html><body>'
    b'<a href="https://www.bis.org/publications/qr-202609.pdf">Full issue</a>'
    b'<a href="https://www.bis.org/publications/markets-take-stock_1.pdf">PDF</a>'
    b'</body></html>'
)


def test_bis_quarterly_discovers_new_issue_and_article_pdfs():
    base = "https://www.bis.org/publications/"
    client = _FakeClient(
        pages={f"{base}qr-202609": _BIS_ISSUE,
               f"{base}qr-202609/markets-take-stock": _BIS_ARTICLE,
               f"{base}qr-202609/no-pdf-yet": b"<html><body>none</body></html>"},
        heads={f"{base}qr-202609.pdf"})
    handler = BisQuarterlyHandler(_endpoint("tier4_intl", "bis_quarterly"),
                                  _ctx(4, client))

    items = handler.discover()

    assert [i.url for i in items] == [f"{base}qr-202609.pdf",
                                      f"{base}markets-take-stock_1.pdf"]
    assert [i.doc_type for i in items] == ["bis_quarterly_full", "bis_quarterly_article"]
    assert all(i.is_pdf and i.extra["release_date"] == date(2026, 9, 1) for i in items)


def test_bis_annual_uses_new_url_scheme():
    cfg = _endpoint("tier4_intl", "bis_annual")
    assert (cfg.content["pdf_url_pattern"].replace("{YYYY}", "2026")
            == "https://www.bis.org/publications/aer-2026.pdf")


# ── TSMC ─────────────────────────────────────────────────────────────────────

_TSMC_INDEX = (
    b'<html><body>'
    b'<a href="/english/latest-news">Latest News</a>'
    b'<a href="/chinese/latest-news">Chinese</a>'
    b'<a href="/english/news/3340">TSMC August 2026 Revenue Report</a>'
    b'<a href="/english/news/3338">ASML and TSMC Announce Initiative</a>'
    b'</body></html>'
)
# Long undated nav pushes the dateline past dateextract's page-head scan.
_TSMC_NAV = b"".join(b"<li><a href='/x%d'>Menu item %d</a></li>" % (i, i)
                     for i in range(400))
_TSMC_ARTICLE = (
    b"<html><head><title>TSMC</title></head><body><a href='#main'>Skip to main "
    b"content</a> Search<nav><ul>" + _TSMC_NAV + b"</ul></nav><main><article>"
    b"<p>Issued by: TSMC Issued on: 2026/09/10</p>"
    b"<p>HSINCHU, Taiwan, R.O.C. - September 10, 2026 - TSMC today announced its "
    b"net revenue for August 2026: On a consolidated basis, revenue for August "
    b"2026 was approximately NT$514.81 billion, an increase of 10.1 percent from "
    b"July 2026 driven by high-performance computing and AI accelerator demand.</p>"
    b"<p>Revenue for January through August 2026 increased year-over-year as "
    b"advanced-node and packaging capacity stayed fully utilised.</p>"
    b"</article></main><div class='cookie-banner'>We use cookies. Please refer to "
    b"Cookie Policy for more information. Save and Close</div></body></html>"
)


def test_tsmc_discovers_releases_on_new_site():
    cfg = _endpoint("tier6b_chips", "tsmc")
    client = _FakeClient(pages={cfg.discovery.url: _TSMC_INDEX})
    handler = TsmcHandler(cfg, _ctx(6, client))

    assert [i.url for i in handler.discover()] == [
        "https://pr.tsmc.com/english/news/3340", "https://pr.tsmc.com/english/news/3338"]


def test_tsmc_dates_release_from_article_body():
    url = "https://pr.tsmc.com/english/news/3340"
    cfg = _endpoint("tier6b_chips", "tsmc")
    handler = TsmcHandler(cfg, _ctx(6, _FakeClient(pages={url: _TSMC_ARTICLE})))
    item = next(i for i in TsmcHandler(
        cfg, _ctx(6, _FakeClient(pages={cfg.discovery.url: _TSMC_INDEX}))).discover()
        if i.url == url)

    [doc] = handler.fetch(item)

    assert doc.release_date == date(2026, 9, 10)
    assert doc.fields.get("revenue_ntd_billion") == "514.81"
    text = " ".join(c.text for c in doc.chunks)
    assert text.startswith("Issued by: TSMC")
    for chrome in ("Skip to main", "Menu item", "Cookie Policy"):
        assert chrome not in text


# ── SLOOS ────────────────────────────────────────────────────────────────────

_SLOOS_LANDING = (
    b'<html><body>'
    b'<a href="/data/sloos/about.htm">About</a>'
    b'<a href="/data/sloos/201705/default.htm">May 2017</a>'
    b'<a href="/data/sloos/sloos-202510.htm">October 2025</a>'
    b'<a href="/data/sloos/sloos-202601.htm">January 2026</a>'
    b'<a href="/data/sloos/sloos-202607.htm">July 2026</a>'
    b'<a href="/data/sloos/sloos-202504.htm">April 2025</a>'
    b'<a href="/data/sloos/sloos-202604.htm">April 2026</a>'
    b'</body></html>'
)
_SLOOS_RELEASE = (
    b"<html><body><div class='usa-banner'>An official website of the United States "
    b"Government. Here's how you know. Official websites use .gov</div>"
    b"<div id='content' role='main'><ol class='breadcrumb'><li>Home</li><li>Data</li>"
    b"</ol><ul><li>Release Dates</li><li>Announcements</li><li>About</li></ul>"
    b"<div class='row'><div class='col-xs-12 col-sm-12 col-md-12'>"
    b"<h3>Current Survey <a href='/data/documents/sloos-202607.pdf'>PDF</a></h3>"
    b"<p><a href='/t1.htm'>Table 1</a> | <a href='/t2.htm'>Table 2</a></p>"
    b"<h3>The July 2026 Senior Loan Officer Opinion Survey on Bank Lending Practices</h3>"
    b"<p>The July 2026 Senior Loan Officer Opinion Survey on Bank Lending Practices "
    b"addressed changes in the standards and terms on, and demand for, bank loans "
    b"to businesses and households over the past three months, which generally "
    b"correspond to the second quarter of 2026.</p>"
    b"<p>Responses were received from 66 domestic banks between June 17, 2026 and "
    b"July 2, 2026. Banks reported tighter standards for commercial and industrial "
    b"loans and weaker demand across firm sizes.</p>"
    b"</div></div></div><div class='lastUpdate' id=\"lastUpdate\">Last Update:\n"
    b"                August 03, 2026\n                </div></body></html>"
)


def test_sloos_discovers_newest_releases_from_landing_page():
    cfg = _endpoint("tier1_fed", "sloos")
    client = _FakeClient(pages={cfg.discovery.url: _SLOOS_LANDING})
    items = SloosHandler(cfg, _ctx(1, client)).discover()

    base = "https://www.federalreserve.gov/data/sloos/"
    assert [i.url for i in items] == [f"{base}sloos-202607.htm", f"{base}sloos-202604.htm",
                                      f"{base}sloos-202601.htm", f"{base}sloos-202510.htm"]
    assert {i.doc_type for i in items} == {"sloos_release"}


def test_sloos_release_dated_by_footer_not_survey_month():
    url = "https://www.federalreserve.gov/data/sloos/sloos-202607.htm"
    cfg = _endpoint("tier1_fed", "sloos")
    handler = SloosHandler(cfg, _ctx(1, _FakeClient(pages={url: _SLOOS_RELEASE})))
    item = next(i for i in SloosHandler(
        cfg, _ctx(1, _FakeClient(pages={cfg.discovery.url: _SLOOS_LANDING}))).discover()
        if i.url == url)

    [doc] = handler.fetch(item)

    # Published 2026-08-03; the URL's 202607 and the body's June/July field dates
    # would all predate publication and leak into ex-ante backtests.
    assert doc.release_date == date(2026, 8, 3)
    # Body starts at the release title: no .gov banner, nav or download links.
    text = " ".join(c.text for c in doc.chunks)
    assert doc.chunks[0].text.startswith("The July 2026 Senior Loan Officer Opinion Survey")
    for chrome in ("official website", "Release Dates", "Current Survey", "Table 1"):
        assert chrome not in text


def test_date_from_last_update():
    assert date_from_last_update(b"<div>Last Update:\n  January 9, 2026\n</div>") \
        == date(2026, 1, 9)
    assert date_from_last_update("<div>no footer here</div>") is None


# ── Morgan Stanley meta date ─────────────────────────────────────────────────

def test_content_published_at_meta_is_a_publication_date():
    html = (b'<html><head><meta name="content_publishedAt" '
            b'content="2026-10-02T04:00:00Z" type="Date"></head>'
            b'<body><p>Andrew Sheets on rising bond yields and equity valuations.</p>'
            b'</body></html>')
    assert dateextract.extract_date_from_html(html, max_date=TODAY) == date(2026, 10, 2)


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_query_no_llm_prints_candidates_without_rerank_score(monkeypatch, capsys):
    from pipelines import cli, query_pipeline
    from utils.schema import RetrievedChunk

    chunks = [
        RetrievedChunk(chunk_id="reranked::000", text="reranked", score=0.02,
                       payload={"publisher": "Fed"}, rerank_score=0.5),
        RetrievedChunk(chunk_id="past-cut::000", text="past the rerank cut", score=0.03,
                       payload={"publisher": "TSMC"}),
    ]
    retriever = SimpleNamespace(retrieve=lambda q: (
        chunks, SimpleNamespace(route="DIRECT", filters=None)))
    monkeypatch.setattr(cli, "load_settings", lambda: None)
    monkeypatch.setattr(query_pipeline, "build_retriever", lambda settings: retriever)

    assert cli._cmd_query(SimpleNamespace(question="q", no_llm=True)) == 0

    out = capsys.readouterr().out
    assert "[0.500] Fed" in out
    assert "[0.030] TSMC" in out


# ── logging ──────────────────────────────────────────────────────────────────

def test_ingest_logging_silences_request_url_logs(monkeypatch):
    monkeypatch.setattr(logging_utils, "_CONFIGURED", False)
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)

    logging_utils.setup_logging("INFO")

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
