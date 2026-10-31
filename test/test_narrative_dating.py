"""Remaining narrative handlers (chips SIA/TSMC) now stamp a derived release_date
so their chunks pass the synthesis citation gate (offline; fake HTTP client)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from ingest.chips.handlers.sia import SiaHandler
from ingest.chips.handlers.tsmc import TsmcHandler
from ingest.common.chunking import Chunker
from ingest.common.config import ChunkingSettings, DiscoveryConfig, EndpointConfig
from ingest.common.http_client import sha256_hex
from ingest.common.models import DiscoveredItem, FetchResult
from ingest.fed.handlers.sloos import SloosHandler


class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content

    def get(self, url, use_cache=True):
        return FetchResult(url=url, status_code=200, content=self._content,
                           content_type="text/html",
                           content_hash=sha256_hex(self._content))


class _FakeCache:
    def is_processed(self, url, content_hash):
        return False


class _FakeRaw:
    def save(self, *a, **k):
        return "raw/path"


def _ctx(content: bytes):
    tier = SimpleNamespace(tier=6, publisher="Chips", licence="public_research_free")
    return SimpleNamespace(
        tier=tier, client=_FakeClient(content), cache=_FakeCache(),
        raw_store=_FakeRaw(), chunker=Chunker(ChunkingSettings()),
        today=date(2026, 7, 1),
        settings=SimpleNamespace(http=SimpleNamespace(user_agent="test")),
        refresh=False,
    )


def _cfg(handler: str, content: dict | None = None):
    return EndpointConfig(
        id=f"test_{handler}", handler=handler, title="Test Chips", bucket="C",
        discovery=DiscoveryConfig(type="html_index", url="https://chips.test/index"),
        content=content or {},
    )


_SIA_HTML = (
    b'<html><head><meta property="article:published_time" content="2025-11-03">'
    b'</head><body><article>'
    b'<p>The Semiconductor Industry Association today announced that global '
    b'semiconductor sales were $62.1 billion, an increase of 20 percent compared '
    b'to the same month a year earlier, reflecting continued AI-driven demand.</p>'
    b'<p>Sales rose across all regions as data-center and accelerator demand '
    b'stayed robust through the quarter.</p></article></body></html>'
)


def test_sia_html_stamps_derived_date_and_keeps_fields():
    handler = SiaHandler(_cfg("sia"), _ctx(_SIA_HTML))
    item = DiscoveredItem(source_id="test_sia", title="SIA monthly",
                          url="https://chips.test/global-semiconductor-sales-nov",
                          doc_type="sia_monthly", chunking="whole")
    [doc] = handler.fetch(item)
    assert doc.release_date == date(2025, 11, 3)
    assert doc.chunks and all(c.metadata.release_date == date(2025, 11, 3)
                              for c in doc.chunks)
    assert doc.fields.get("sales_usd_billion") == "62.1"  # parser still runs


_TSMC_HTML = (
    b'<html><head><meta property="article:published_time" content="2026-06-10">'
    b'</head><body><article>'
    b'<p>TSMC today reported consolidated revenue of NT$260.1 billion for the '
    b'month, which increased 38.6% year-over-year on strong high-performance '
    b'computing and AI accelerator demand.</p>'
    b'<p>Management reiterated its full-year capex plans centered on advanced '
    b'nodes and packaging capacity.</p></article></body></html>'
)


def test_tsmc_html_stamps_derived_date():
    handler = TsmcHandler(_cfg("tsmc"), _ctx(_TSMC_HTML))
    item = DiscoveredItem(source_id="test_tsmc", title="TSMC release",
                          url="https://chips.test/tsmc-monthly", doc_type="tsmc_release",
                          chunking="whole")
    [doc] = handler.fetch(item)
    assert doc.release_date == date(2026, 6, 10)
    assert doc.chunks
    assert doc.fields.get("revenue_ntd_billion") == "260.1"


_SLOOS_HTML = (
    b"<html><body><article>"
    b"<p>The January 27, 2026 Senior Loan Officer Opinion Survey on Bank Lending "
    b"Practices addressed changes in the standards and terms on, and demand for, "
    b"bank loans to businesses and households over the past three months.</p>"
    b"<p>Banks reported tighter standards and weaker demand for commercial and "
    b"industrial loans across firm sizes during the fourth quarter.</p>"
    b"</article></body></html>"
)


def test_sloos_derives_date_from_body_when_url_and_feed_lack_it():
    # url has no date and the RSS item carries no published date -> derive from body
    tier1 = SimpleNamespace(tier=1, publisher="Federal Reserve",
                            licence="public_domain_us_gov")
    ctx = SimpleNamespace(
        tier=tier1, client=_FakeClient(_SLOOS_HTML), cache=_FakeCache(),
        raw_store=_FakeRaw(), chunker=Chunker(ChunkingSettings()),
        today=date(2026, 7, 1),
        settings=SimpleNamespace(http=SimpleNamespace(user_agent="test")),
        refresh=False,
    )
    handler = SloosHandler(_cfg("sloos"), ctx)
    item = DiscoveredItem(source_id="test_sloos", title="SLOOS",
                          url="https://fed.test/sloos/latest", doc_type="sloos_release",
                          chunking="whole", is_pdf=False, published=None)
    [doc] = handler.fetch(item)
    assert doc.release_date == date(2026, 1, 27)
    assert doc.chunks
