"""Facet filtering + heuristic query analysis."""

from __future__ import annotations

from retriever.analysis import HeuristicAnalyzer, extract_date_window, extract_filters
from utils.schema import MetaFilter
from vectorstores.filters import match_payload


def _payload(**kw):
    base = {"tier": 1, "bucket": "B", "doc_type": "fomc_statement",
            "publisher": "Federal Reserve", "entity_tags": ["ai"],
            "factor_tags": ["discount_rate"], "release_date": "2026-06-17"}
    base.update(kw)
    return base


def test_match_payload_empty_filter_passes():
    assert match_payload(MetaFilter(), _payload())


def test_match_payload_tier_and_doctype():
    assert match_payload(MetaFilter(tiers=[1], doc_types=["fomc_statement"]), _payload())
    assert not match_payload(MetaFilter(tiers=[2]), _payload())


def test_match_payload_tag_is_any_overlap():
    assert match_payload(MetaFilter(factor_tags=["discount_rate", "fx"]), _payload())
    assert not match_payload(MetaFilter(factor_tags=["fx"]), _payload())


def test_match_payload_date_window():
    assert match_payload(MetaFilter(date_from="2026-01-01", date_to="2026-12-31"), _payload())
    assert not match_payload(MetaFilter(date_from="2027-01-01"), _payload())


def test_extract_date_window_single_and_range():
    assert extract_date_window("what happened in 2026?") == ("2026-01-01", "2026-12-31")
    assert extract_date_window("from 2024 to 2026") == ("2024-01-01", "2026-12-31")
    assert extract_date_window("no year here") == (None, None)


def test_extract_filters_picks_up_tags():
    mf = extract_filters("What is the outlook for inflation and the credit spread?")
    assert "inflation" in mf.factor_tags
    assert "credit_spread" in mf.factor_tags


def test_extract_filters_entity_and_synonym():
    mf = extract_filters("export controls on semiconductors to China")
    assert "china" in mf.entity_tags and "semiconductors" in mf.entity_tags
    assert "export_controls" in mf.factor_tags


def test_heuristic_analyzer_returns_direct_route():
    qa = HeuristicAnalyzer().analyze("AI capex risk in 2026")
    assert qa.route == "DIRECT"
    assert "ai_capex" in qa.filters.factor_tags
    assert qa.search_queries() == ["AI capex risk in 2026"]
