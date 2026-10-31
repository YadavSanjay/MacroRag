"""C3: tier3_usstats.yaml configures acm_daily; the registry resolves `acm`."""

from __future__ import annotations

import pytest
import yaml

URL = ("https://www.newyorkfed.org/medialibrary/media/research/data_indicators/"
       "ACMTermPremium.xls")


def _endpoints(acm) -> list[dict]:
    data = yaml.safe_load((acm.REPO_ROOT / "configs" / "ingest" / "tier3_usstats.yaml")
                          .read_text(encoding="utf-8")) or {}
    return [ep for ep in data.get("endpoints") or [] if isinstance(ep, dict)]


@pytest.mark.criterion("C3")
def test_acm_daily_endpoint_as_specified(acm):
    # oracle: spec C3 — handler, URL, sheet, series, backfill start, publisher, and its
    # place before market_episodes.
    eps = _endpoints(acm)
    ids = [e.get("id") for e in eps]
    assert "acm_daily" in ids, "no acm_daily endpoint"
    ep = eps[ids.index("acm_daily")]
    assert ep.get("handler") == "acm"
    assert (ep.get("discovery") or {}).get("url") == URL
    content = ep.get("content") or {}
    assert content.get("sheet") == "ACM Daily"
    assert content.get("series") == ["ACMTP10"]
    assert str(content.get("backfill_start")) == "2005-01-01"
    assert ep.get("publisher") == "Federal Reserve Bank of New York"
    assert ids.index("acm_daily") < ids.index("market_episodes")


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_registry_and_typed_config_agree(acm):
    # oracle: spec C3. Relation: the typed loader yields the same endpoint as the raw YAML,
    # and the tier registry resolves its handler to AcmDailyHandler.
    from ingest.common.config import load_tier_config
    from ingest.usstats.handlers.acm import AcmDailyHandler
    from ingest.usstats.ingestor import UsStatsIngestor
    typed = {e.id: e for e in load_tier_config("tier3_usstats").endpoints}
    assert "acm_daily" in typed, "no acm_daily endpoint"
    assert typed["acm_daily"].content.get("series") == ["ACMTP10"]
    assert UsStatsIngestor.handler_registry.get(typed["acm_daily"].handler) is AcmDailyHandler
