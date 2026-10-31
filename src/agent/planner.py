"""Typed planner: a cheap heuristic gate decides whether a query is
quantitative, then an LLM call produces the typed plan (parsed defensively)."""

from __future__ import annotations

import re
from typing import Optional

from llm.base import LLMClient
from llm.registry import get_registry
from llm.usage import prompt_context, tier_context
from utils.logging import get_logger

from .schema import ComputeStep, DataStep, Plan, RetrieveStep

log = get_logger(__name__)

# Cue words that suggest a numeric answer or arithmetic — gates the planner LLM
# call so purely narrative queries skip it entirely.
_QUANT = re.compile(
    r"\b(how much|how many|change|difference|compare|comparison|peak|trough|"
    r"increase|decrease|rise|fall|fell|rose|percent|percentage|ppt|p\.?p\.?|"
    r"average|sum|ratio|times|multiple|cumulative|growth|bps|basis points|"
    r"path|level|value|rate)\b", re.IGNORECASE)


def looks_quantitative(query: str) -> bool:
    return bool(_QUANT.search(query or ""))


def _steps(items, ctor, **defaults):
    out = []
    for i, raw in enumerate(items or []):
        if not isinstance(raw, dict):
            continue
        raw = {**defaults, **raw}
        raw.setdefault("id", f"s{i}")
        try:
            out.append(ctor(**{k: raw.get(k) for k in ctor.__dataclass_fields__
                               if k in raw}))
        except TypeError:
            continue
    return out


def parse_plan(data: dict) -> Plan:
    if not data.get("quantitative"):
        return Plan(quantitative=False)
    return Plan(
        quantitative=True,
        data=_steps(data.get("data"), DataStep, aggregate="value"),
        retrieve=_steps(data.get("retrieve"), RetrieveStep),
        compute=_steps(data.get("compute"), ComputeStep, label=""),
    )


class LLMPlanner:
    def __init__(self, llm: LLMClient, *, model: str | None = None,
                 sources: list[str] | None = None):
        self.llm = llm
        self.model = model
        self.sources = sources or []

    def plan(self, query: str) -> Plan:
        if not looks_quantitative(query):
            return Plan(quantitative=False)
        src = ", ".join(self.sources[:40]) or "(none indexed)"
        try:
            rp = get_registry().resolve("plan_execute", model=self.model)
            with prompt_context(rp.variant_id, rp.content_hash), tier_context("plan"):
                parsed = self.llm.complete_json(
                    system=rp.system, model=self.model, purpose="plan_execute",
                    schema=rp.schema, user=rp.render(question=query, sources=src))
        except Exception as exc:        # never let planning break the query
            log.warning("planning failed (%s); falling back to RAG", exc)
            return Plan(quantitative=False)
        return parse_plan(parsed)
