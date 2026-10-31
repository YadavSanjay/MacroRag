"""Cheap, rule-based query analysis: extract MacroRAG facet filters (factor/
entity tags, date window) straight from the query text using the controlled
vocabulary. This is the always-on first pass; the LLM analyzer (llm package) can
add paraphrase/decompose/HyDE on top, but filter extraction stays deterministic.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

import yaml

from utils.config import REPO_ROOT
from utils.schema import MetaFilter, QueryAnalysis

_VOCAB_PATH = REPO_ROOT / "configs" / "ingest" / "vocabulary.yaml"
_YEAR = re.compile(r"\b(19|20)\d{2}\b")

# Natural-language cues -> canonical tags (beyond literal tag-name matches).
_FACTOR_SYNONYMS = {
    "rate hike": "discount_rate", "rate cut": "discount_rate",
    "fed funds": "discount_rate", "policy rate": "discount_rate",
    "term premium": "term_premium", "yield curve": "term_premium",
    "credit spread": "credit_spread", "spreads": "credit_spread",
    "inflation": "inflation", "cpi": "inflation", "pce": "inflation",
    "unemployment": "growth", "gdp": "growth", "recession": "growth",
    "ai capex": "ai_capex", "capex": "ai_capex", "data center": "data_center_power",
    "power": "data_center_power", "electricity": "electricity_demand",
    "export control": "export_controls", "tariff": "export_controls",
    "taiwan": "china_taiwan_tail", "geopolitical": "geopolitical_risk",
    "policy uncertainty": "policy_uncertainty", "valuation": "valuation_stretch",
}
_ENTITY_SYNONYMS = {
    "ai": "ai", "artificial intelligence": "ai", "hyperscaler": "hyperscaler",
    "data center": "data_center", "semiconductor": "semiconductors",
    "chip": "semiconductors", "china": "china", "quantum": "quantum",
}


@lru_cache(maxsize=1)
def _vocab() -> tuple[set[str], set[str]]:
    data = yaml.safe_load(_VOCAB_PATH.read_text(encoding="utf-8")) or {}
    return set(data.get("factor_tags", [])), set(data.get("entity_tags", []))


@lru_cache(maxsize=512)
def _cue_pattern(phrase: str) -> re.Pattern:
    """Whole-word match for a cue, allowing one plural ``s`` — a bare substring
    test let ``ai`` fire inside "maritime/air", "Strait" and "chains"."""
    return re.compile(rf"\b{re.escape(phrase)}s?\b")


def _match_tags(query: str, allowed: set[str], synonyms: dict[str, str]) -> list[str]:
    q = query.lower()
    found: list[str] = []
    for tag in allowed:                       # literal tag name e.g. "credit_spread"
        if _cue_pattern(tag.replace("_", " ")).search(q) and tag not in found:
            found.append(tag)
    for phrase, tag in synonyms.items():       # natural-language cue
        if _cue_pattern(phrase).search(q) and tag in allowed and tag not in found:
            found.append(tag)
    return found


def extract_date_window(query: str) -> tuple[str | None, str | None]:
    years = sorted({int(m.group()) for m in _YEAR.finditer(query)})
    if not years:
        return None, None
    return f"{years[0]}-01-01", f"{years[-1]}-12-31"


def extract_filters(query: str, base: MetaFilter | None = None) -> MetaFilter:
    factors, entities = _vocab()
    mf = MetaFilter(
        factor_tags=_match_tags(query, factors, _FACTOR_SYNONYMS),
        entity_tags=_match_tags(query, entities, _ENTITY_SYNONYMS),
    )
    mf.date_from, mf.date_to = extract_date_window(query)
    if base:                                   # caller-supplied filters win/merge
        mf.tiers = base.tiers or mf.tiers
        mf.buckets = base.buckets or mf.buckets
        mf.doc_types = base.doc_types or mf.doc_types
        mf.publishers = base.publishers or mf.publishers
        mf.factor_tags = base.factor_tags or mf.factor_tags
        mf.entity_tags = base.entity_tags or mf.entity_tags
        mf.date_from = base.date_from or mf.date_from
        mf.date_to = base.date_to or mf.date_to
        # The exemption only softens heuristic entity tags; caller ones stay hard.
        if not base.entity_tags:
            mf.entity_exempt_factor_tags = list(base.entity_exempt_factor_tags)
    return mf


class HeuristicAnalyzer:
    """Filter-only analyzer (no LLM). Always available; safe default."""

    def analyze(self, query: str, *, base_filter: MetaFilter | None = None
                ) -> QueryAnalysis:
        return QueryAnalysis(original=query, route="DIRECT",
                             filters=extract_filters(query, base_filter))
