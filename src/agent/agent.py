"""PlanExecuteAgent: ties the typed planner to the executor. ``maybe_plan``
returns a plan only when the query is quantitative (else None -> the pipeline
takes the normal RAG path)."""

from __future__ import annotations

from typing import Optional

from .executor import PlanExecutor
from .planner import LLMPlanner
from .schema import Plan, PlanResult


class PlanExecuteAgent:
    def __init__(self, planner: LLMPlanner, executor: PlanExecutor,
                 *, enabled: bool = True):
        self.planner = planner
        self.executor = executor
        self.enabled = enabled

    def maybe_plan(self, query: str) -> Plan | None:
        if not self.enabled:
            return None
        plan = self.planner.plan(query)
        return plan if plan.quantitative else None

    def execute(self, plan: Plan, query: str) -> PlanResult:
        return self.executor.execute(plan, query)
