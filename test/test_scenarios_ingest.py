"""Tier 7 (scenario / stress-test) ingestion tests — offline, no network/torch.

Covers the three things a new pinned-document source must get right for the
shock-vector synthesizer to be able to cite it:
  1. the tier config loads and wires onto a registered handler/ingestor;
  2. every pinned file carries a parseable release_date (the citation gate needs it);
  3. discover()/fetch() produce dated, chunked documents from fetched bytes.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from ingest import TIER_INGESTORS
from ingest.common.chunking import Chunker
from ingest.common.config import (
    ChunkingSettings,
    DiscoveryConfig,
    EndpointConfig,
    load_tier_config,
    load_vocabulary,
)
from ingest.common.http_client import sha256_hex
from ingest.common.models import FetchResult
from ingest.scenarios import ScenariosIngestor
from ingest.scenarios.handlers import HANDLERS
from ingest.scenarios.handlers.pinned_docs import PinnedDocsHandler, _is_pdf, _parse_date

TIER_NAME = "tier7_scenarios"


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content

    def get(self, url, use_cache=True):
        return FetchResult(url=url, status_code=200, content=self._content,
                           content_type="text/html",
                           content_hash=sha256_hex(self._content))

    def head(self, url):
        return 200


class _FakeCache:
    def is_processed(self, url, content_hash):
        return False

    def mark_processed(self, *a, **k):
        pass


class _FakeRaw:
    def save(self, *a, **k):
        return "raw/path"


def _ctx(content: bytes, *, today=date(2025, 7, 1)):
    tier = SimpleNamespace(tier=7, publisher="Scenario & Stress-Test Sources",
                           licence="public_research_free")
    return SimpleNamespace(
        tier=tier, client=_FakeClient(content), cache=_FakeCache(),
        raw_store=_FakeRaw(), chunker=Chunker(ChunkingSettings()), today=today,
        settings=SimpleNamespace(http=SimpleNamespace(user_agent="test")),
        refresh=False,
    )


def _cfg(files):
    return EndpointConfig(
        id="test_scenarios", handler="pinned_docs", title="Test Scenarios",
        publisher="Test Publisher", licence="public_research_free", bucket="B",
        discovery=DiscoveryConfig(type="predictable_url",
                                  url="https://example.test/scenarios"),
        factor_tags=["cross_asset_scenario"], entity_tags=[],
        content={"files": files},
    )


_HTML = (
    b"<html><body><article>"
    b"<h1>Adverse Scenario Narrative</h1>"
    b"<p>The adverse scenario assumes an aggravation of geopolitical tensions "
    b"and trade fragmentation that triggers a broad-based repricing of risk "
    b"across global markets over the projection horizon.</p>"
    b"<p>Long-term sovereign yields rise, corporate credit spreads widen "
    b"materially, equity prices fall sharply and market volatility spikes as "
    b"investors reprice a sustained deterioration in the macro outlook.</p>"
    b"<p>Currency markets are also disrupted, with safe-haven flows supporting "
    b"the dollar while more vulnerable currencies depreciate under capital "
    b"outflows and tightening financial conditions.</p>"
    b"</article></body></html>"
)


# ── config + wiring ──────────────────────────────────────────────────────────

def test_tier7_registered_and_handler_present():
    assert TIER_INGESTORS[TIER_NAME] is ScenariosIngestor
    assert "pinned_docs" in HANDLERS
    assert "pinned_docs" in ScenariosIngestor.handler_registry


def test_tier7_config_loads_and_files_are_dated():
    tier = load_tier_config(TIER_NAME)
    assert tier.tier == 7
    assert tier.endpoints, "tier 7 must define at least one endpoint"
    for ep in tier.endpoints:
        assert ep.handler in HANDLERS, f"{ep.id} references unknown handler {ep.handler}"
        files = ep.content.get("files", [])
        assert files, f"{ep.id} must pin at least one file"
        for f in files:
            assert f["url"].startswith("http"), f"{ep.id}: bad url {f.get('url')}"
            # every pinned file MUST be dated or its chunks fail the citation gate
            assert _parse_date(f.get("release_date")) is not None, \
                f"{ep.id}: file {f['url']} has no valid release_date"


def test_tier7_covers_taiwan_conflict_studies():
    tier = load_tier_config(TIER_NAME)
    ids = {ep.id for ep in tier.endpoints}
    # Wave 2 — the specific tail the portfolio is exposed to
    assert {"stlouisfed_taiwan", "rhodium_taiwan_crisis",
            "hcss_taiwan_cost", "csis_taiwan_impact"} <= ids


def test_thinktank_sources_carry_restricted_licence():
    tier = load_tier_config(TIER_NAME)
    by_id = {ep.id: ep for ep in tier.endpoints}
    for eid in ("rhodium_taiwan_crisis", "hcss_taiwan_cost", "csis_taiwan_impact"):
        assert by_id[eid].licence == "internal_analytical_use", eid
    # the St. Louis Fed study is a US public-domain publication
    assert by_id["stlouisfed_taiwan"].licence == "public_domain_us_gov"


def test_new_geopolitical_tags_registered():
    factor_tags = load_vocabulary()["factor_tags"]
    for tag in ("cross_asset_scenario", "stress_scenario", "supply_chain_disruption",
                "sanctions_regime", "flight_to_quality"):
        assert tag in factor_tags, f"{tag} missing from vocabulary registry"


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_is_pdf_detection():
    assert _is_pdf({"url": "https://x/report.pdf"}) is True
    assert _is_pdf({"url": "https://x/report.pdf?v=2"}) is True
    assert _is_pdf({"url": "https://x/analysis"}) is False
    assert _is_pdf({"url": "https://x/analysis", "is_pdf": True}) is True
    assert _is_pdf({"url": "https://x/report.pdf", "is_pdf": False}) is False


def test_parse_date_bad_value_is_none():
    assert _parse_date("not-a-date") is None
    assert _parse_date(None) is None
    assert _parse_date("2025-04-22") == date(2025, 4, 22)


# ── discover / fetch behaviour ───────────────────────────────────────────────

def test_discover_classifies_pdf_and_html():
    cfg = _cfg([
        {"url": "https://example.test/scenario.pdf", "publication": "PDF Scenario",
         "release_date": "2025-01-14"},
        {"url": "https://example.test/analysis", "publication": "HTML Scenario",
         "release_date": "2025-04-22", "is_pdf": False},
    ])
    handler = PinnedDocsHandler(cfg, _ctx(b""))
    items = handler.discover()
    assert len(items) == 2
    pdf_item, html_item = items
    assert pdf_item.is_pdf and pdf_item.chunking == "pdf_sections"
    assert not html_item.is_pdf and html_item.chunking == "sections"
    assert html_item.title == "HTML Scenario"


def test_fetch_html_produces_dated_citable_chunks():
    cfg = _cfg([
        {"url": "https://example.test/analysis", "publication": "HTML Scenario",
         "release_date": "2025-03-01", "is_pdf": False,
         "scenario_family": "regulatory_adverse"},
    ])
    handler = PinnedDocsHandler(cfg, _ctx(_HTML))
    [item] = handler.discover()
    docs = handler.fetch(item)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.release_date == date(2025, 3, 1)
    assert doc.publication == "HTML Scenario"
    assert doc.tier == 7
    assert doc.chunks, "expected at least one chunk from the article body"
    # every chunk carries the release_date -> passes the gate's dated-citation rule
    for ch in doc.chunks:
        assert ch.metadata.release_date == date(2025, 3, 1)
    assert doc.fields.get("scenario_family") == "regulatory_adverse"


def test_fetch_html_without_date_still_ingests_but_undated():
    cfg = _cfg([
        {"url": "https://example.test/analysis", "publication": "Undated",
         "is_pdf": False},
    ])
    handler = PinnedDocsHandler(cfg, _ctx(_HTML))
    [item] = handler.discover()
    docs = handler.fetch(item)
    assert len(docs) == 1
    assert docs[0].release_date is None  # would be dropped by the citation gate
