"""Execute a typed plan: structured data lookups first (time-series store),
deterministic arithmetic (calculator), then narrative RAG. Returns the evidence
bundle the generator composes the cited answer from."""

from __future__ import annotations

from typing import Optional

from retriever.fusion import dedup_by_chunk
from utils.logging import get_logger
from utils.schema import TimeseriesHit

from .calculator import CalculatorError, evaluate
from .schema import DataStep, Plan, PlanResult

log = get_logger(__name__)


class PlanExecutor:
    def __init__(self, retriever, timeseries):
        self.retriever = retriever
        self.timeseries = timeseries

    def execute(self, plan: Plan, query: str) -> PlanResult:
        values: dict[str, float] = {}
        data_hits: list[TimeseriesHit] = []
        for step in plan.data:
            scalar, hit = self._reduce(step)
            if scalar is not None:
                values[step.id] = scalar
            if hit is not None:
                data_hits.append(hit)

        computations = []
        for c in plan.compute:
            try:
                val = evaluate(c.expression, values)
                values[c.id] = val
                computations.append({"id": c.id, "label": c.label or c.id,
                                     "expression": c.expression, "value": round(val, 6)})
            except CalculatorError as exc:
                log.info("compute step %s failed: %s", c.id, exc)
                computations.append({"id": c.id, "label": c.label or c.id,
                                     "expression": c.expression, "error": str(exc)})

        chunks = []
        sub_queries = [r.query for r in plan.retrieve] or [query]  # narrative context
        for q in sub_queries:
            cs, _ = self.retriever.retrieve(q)
            chunks.extend(cs)
        chunks = dedup_by_chunk(chunks)

        return PlanResult(plan=plan, values=values, data_hits=data_hits,
                          computations=computations, chunks=chunks)

    # ── data lookup -> scalar ────────────────────────────────────────────────

    def _reduce(self, step: DataStep) -> tuple[float | None, TimeseriesHit | None]:
        if self.timeseries is None:
            return None, None
        rows = self.timeseries.query(source_like=step.source_like,
                                     variable=step.variable, scenario=step.scenario)
        if step.statistic:
            rows = [r for r in rows if r.get("statistic") == step.statistic]
        series = [r for r in rows if r.get("value") is not None]
        if step.period and step.aggregate == "value":
            matched = [r for r in series if str(r.get("period")) == step.period]
            series = matched or series
        vals = [float(r["value"]) for r in series]
        if not vals:
            return None, None
        scalar = {
            "value": vals[0] if step.period else vals[-1],
            "first": vals[0], "last": vals[-1],
            "max": max(vals), "min": min(vals),
            "mean": sum(vals) / len(vals), "count": float(len(vals)),
        }.get(step.aggregate, vals[-1])
        hit = TimeseriesHit(
            source=series[0].get("source") or step.source_like,
            publisher=series[0].get("publisher", ""), rows=series[:40],
            note=f"{step.variable or ''} {step.aggregate}".strip())
        return scalar, hit
