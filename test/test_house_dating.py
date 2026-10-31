"""Tier 5 house-view handlers now stamp a derived release_date so their narrative
chunks can pass the synthesis citation gate (offline; fake HTTP client)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from ingest.common.chunking import Chunker
from ingest.common.config import ChunkingSettings
from ingest.common.http_client import sha256_hex
from ingest.common.models import FetchResult
from ingest.house.handlers.blackrock import BlackRockHandler
from ingest.house.handlers.ms_totm import MsTotmHandler


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
    tier = SimpleNamespace(tier=5, publisher="House", licence="public_with_attribution")
    return SimpleNamespace(
        tier=tier, client=_FakeClient(content), cache=_FakeCache(),
        raw_store=_FakeRaw(), chunker=Chunker(ChunkingSettings()),
        today=date(2026, 7, 1),
        settings=SimpleNamespace(http=SimpleNamespace(user_agent="test")),
        refresh=False,
    )


def _cfg(handler: str, content: dict):
    from ingest.common.config import DiscoveryConfig, EndpointConfig
    return EndpointConfig(
        id=f"test_{handler}", handler=handler, title="Test House", bucket="C",
        discovery=DiscoveryConfig(type="html_index", url="https://house.test/index"),
        content=content,
    )


_BLACKROCK_HTML = (
    b'<html><head>'
    b'<meta property="article:published_time" content="2026-05-11T09:00:00Z">'
    b'</head><body><article>'
    b'<p>Our weekly commentary keeps an overweight to the AI mega force as '
    b'hyperscaler capex marches toward a trillion dollars a year, even as we '
    b'watch power constraints and financing conditions closely.</p>'
    b'<p>We stay pro-risk but acknowledge fatter tails around geopolitics and '
    b'rates volatility over the tactical horizon.</p>'
    b'</article></body></html>'
)


def test_blackrock_html_stamps_derived_date():
    cfg = _cfg("blackrock", {"direct_urls": [{"url": "https://house.test/weekly",
                                              "publication": "BII Weekly"}],
                             "mega_forces": ["ai"]})
    handler = BlackRockHandler(cfg, _ctx(_BLACKROCK_HTML))
    [item] = handler.discover()
    [doc] = handler.fetch(item)
    assert doc.release_date == date(2026, 5, 11)
    assert doc.chunks
    for ch in doc.chunks:
        assert ch.metadata.release_date == date(2026, 5, 11)
    assert "ai" in doc.fields.get("mega_forces_referenced", [])


_MS_HTML = (
    b'<html><head><meta name="date" content="2026-03-20"></head><body><article>'
    b'<p>Intro paragraph about markets.</p>'
    b'<h2>Transcript</h2>'
    b'<p>Andrew Sheets: Welcome to Thoughts on the Market. Today we discuss the '
    b'interplay between AI infrastructure spending and rising Treasury issuance '
    b'and what it means for credit spreads over the next year.</p>'
    b'</article></body></html>'
)


def test_ms_totm_stamps_derived_date():
    cfg = _cfg("ms_totm", {"episode_link_pattern": r"/x/[a-z0-9-]+",
                           "transcript_marker": "Transcript"})
    handler = MsTotmHandler(cfg, _ctx(_MS_HTML))
    # discover() needs a link index; call fetch() directly on a synthetic item
    from ingest.common.models import DiscoveredItem
    item = DiscoveredItem(source_id=cfg.id, title="Episode",
                          url="https://house.test/x/episode", doc_type="ms_totm_episode",
                          chunking="whole", extra={"slug": "episode"})
    [doc] = handler.fetch(item)
    assert doc.release_date == date(2026, 3, 20)
    assert doc.chunks
