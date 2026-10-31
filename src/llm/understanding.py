"""LLM-backed query analyzer: heuristic facet filters (always) + an LLM planning
call that adds route / paraphrase / decompose / HyDE. Drop-in for the retriever's
``Analyzer`` protocol; honours the RetrievalSettings feature flags."""

from __future__ import annotations

from typing import Optional

from retriever.analysis import extract_filters
from utils.config import RetrievalSettings
from utils.logging import get_logger
from utils.schema import MetaFilter, QueryAnalysis

from .base import LLMClient
from .registry import get_registry
from .usage import prompt_context, tier_context

log = get_logger(__name__)


class LLMAnalyzer:
    def __init__(self, llm: LLMClient, settings: RetrievalSettings | None = None,
                 *, plan_model: str | None = None):
        self.llm = llm
        self.s = settings or RetrievalSettings()
        self.plan_model = plan_model     # cheap-tier model for planning

    def analyze(self, query: str, *, base_filter: MetaFilter | None = None
                ) -> QueryAnalysis:
        filters = extract_filters(query, base_filter)
        analysis = QueryAnalysis(original=query, filters=filters)
        if not (self.s.use_router or self.s.use_decompose or self.s.use_hyde):
            return analysis
        try:
            rp = get_registry().resolve("plan", model=self.plan_model)
            with prompt_context(rp.variant_id, rp.content_hash), tier_context("plan"):
                plan = self.llm.complete_json(system=rp.system, model=self.plan_model,
                                              user=rp.render(question=query),
                                              schema=rp.schema, purpose="plan")
        except Exception as exc:  # never let planning break retrieval
            log.warning("query planning failed (%s); falling back to DIRECT", exc)
            return analysis

        analysis.route = str(plan.get("route", "DIRECT")).upper()
        if self.s.use_router and plan.get("paraphrase"):
            analysis.paraphrase = str(plan["paraphrase"])
        if self.s.use_decompose:
            analysis.subqueries = [str(s) for s in (plan.get("subqueries") or []) if s][:5]
        if self.s.use_hyde:
            analysis.hyde_docs = [str(h) for h in (plan.get("hyde") or []) if h][:2]
        return analysis
