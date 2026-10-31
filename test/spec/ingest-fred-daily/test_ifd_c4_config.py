"""C4: tier3_usstats.yaml configures fred_daily and the registry resolves `fred`."""

from __future__ import annotations

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from ingest.usstats.handlers import HANDLERS
from ingest.usstats.handlers.fred import FredDailyHandler

# oracle: the spec's C4 list (copied there from the design note's §4.1 table).
EXPECTED = {
    "VIXCLS": "index_points",
    "BAMLC0A0CM": "percent",
    "BAMLH0A0HYM2": "percent",
    "BAA10Y": "percent",
    "DGS2": "percent",
    "DGS5": "percent",
    "DGS10": "percent",
    "DGS30": "percent",
    "DFII10": "percent",
    "THREEFYTP10": "percent",
    "SOFR": "percent",
    "DEXTAUS": "twd_per_usd",
    "DEXJPUS": "jpy_per_usd",
}


def _endpoint(fred) -> dict | None:
    path = fred.REPO_ROOT / "configs" / "ingest" / "tier3_usstats.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for ep in data.get("endpoints") or []:
        if isinstance(ep, dict) and ep.get("id") == "fred_daily":
            return ep
    return None


def _pairs(series) -> list[tuple[str, str]]:
    """Read `id: units` entries, as {id: X, units: Y} or a one-key {X: Y} mapping."""
    out = []
    for entry in series or []:
        assert isinstance(entry, dict), entry
        if "id" in entry:
            out.append((entry["id"], entry.get("units")))
        else:
            assert len(entry) == 1, entry
            out.extend(entry.items())
    return out


@pytest.mark.criterion("C4")
def test_fred_daily_endpoint_is_configured(fred):
    # oracle: spec C4, copied verbatim (handler, key env, backfill start, 13 pairs).
    ep = _endpoint(fred)
    assert ep is not None, "tier3_usstats.yaml has no fred_daily endpoint"
    assert ep.get("handler") == "fred"
    content = ep.get("content") or {}
    assert content.get("api_key_env") == "FRED_API_KEY"
    assert content.get("backfill_start") == "2005-01-01"
    pairs = _pairs(content.get("series"))
    assert len(pairs) == len(EXPECTED) == 13
    assert dict(pairs) == EXPECTED


@pytest.mark.criterion("C4")
def test_registry_resolves_fred_to_handler():
    # oracle: spec C4 — "the tier's handler registry resolves fred to FredDailyHandler".
    from ingest.usstats.ingestor import UsStatsIngestor
    assert HANDLERS.get("fred") is FredDailyHandler
    assert UsStatsIngestor.handler_registry.get("fred") is FredDailyHandler


@pytest.mark.criterion("C4")
@given(series=st.sampled_from(sorted(EXPECTED)))
def test_property_committed_config_drives_the_handler(fred, series):
    # oracle: C4's list again — the committed fred_daily endpoint, loaded through the
    # typed config and run by the handler the tier registry resolves, requests every
    # configured series (with FRED_API_KEY) and stamps each series' configured units.
    from ingest.common.config import load_tier_config
    from ingest.usstats.ingestor import UsStatsIngestor

    tier = load_tier_config("tier3_usstats")
    eps = [ep for ep in tier.endpoints if ep.id == "fred_daily"]
    assert len(eps) == 1, "tier3_usstats.yaml has no fred_daily endpoint"
    cfg = eps[0]
    assert UsStatsIngestor.handler_registry.get(cfg.handler) is FredDailyHandler

    client = fred.FakeFredClient({}, default=[("2026-03-02", "1.5")])
    docs, raw, client = fred.run(cfg, client, refresh=False, key_env="FRED_API_KEY")
    requested = {r["params"].get("series_id") for r in client.requests}
    assert requested == set(EXPECTED)
    assert all(r["params"].get("api_key") == fred.SENTINEL for r in client.requests)
    doc = fred.by_id(docs)[f"fred_daily_{series.lower()}_2026"]
    fred.assert_rows(doc.timeseries,
                     [fred.row(series, "2026-03-02", 1.5, EXPECTED[series])])
    fred.assert_no_key(docs, raw)
