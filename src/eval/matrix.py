"""Prompt × model regression matrix (design doc §6).

Runs each ``(prompt_variant, model)`` cell over a golden set, scores every cell,
and diffs the result against a stored baseline so a prompt edit or a model bump
can't silently regress quality. The orchestrator (run_matrix / aggregate / diff /
baseline I-O) is pure and unit-tested with a fake runner; the live runners drive
the real pipeline (retrieve → generate/ground → judge) and reuse the Phase 3
``_ground_shock`` helper so any model can be scored.

Metrics are "higher is better" unless named in ``LOWER_BETTER``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from llm.registry import get_registry
from utils.logging import get_logger

log = get_logger(__name__)

# metrics where a *decrease* is an improvement (so an increase is the regression)
LOWER_BETTER = {"n_unsupported", "n_dropped", "repair_rate"}


@dataclass(frozen=True)
class MatrixCell:
    variant: str          # prompt variant_id (or "latest"/"base" for no pin)
    model: str

    @property
    def key(self) -> str:
        return f"{self.variant}::{self.model}"


def cells_from(variants: list[str], models: list[str]) -> list[MatrixCell]:
    """Cartesian product of the variant and model axes."""
    return [MatrixCell(v, m) for v in variants for m in models]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Mean each numeric metric across a cell's per-case rows (non-numeric and
    metadata keys are ignored)."""
    metrics: dict[str, list[float]] = {}
    skip = {"cell", "variant", "model", "case"}
    for r in rows:
        for k, v in r.items():
            if k in skip or isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            metrics.setdefault(k, []).append(float(v))
    return {k: _mean(v) for k, v in metrics.items()}


def run_matrix(task: str, cases: list[dict], cells: list[MatrixCell],
               runner: Callable[[dict, str, str], dict[str, float]],
               *, case_id: Callable[[dict], str] | None = None) -> dict[str, Any]:
    """Run every (cell × case), collect per-case scores from ``runner(case,
    variant, model) -> {metric: value}``, and aggregate per cell. Returns a report:
    ``{task, cells: {cell_key: {metric: mean}}, rows: [...], n_cases}``."""
    cid = case_id or (lambda c: str(c.get("id") or c.get("question", ""))[:60])
    rows: list[dict] = []
    for cell in cells:
        for case in cases:
            scores = runner(case, cell.variant, cell.model) or {}
            rows.append({"cell": cell.key, "variant": cell.variant,
                         "model": cell.model, "case": cid(case), **scores})
    by_cell: dict[str, dict[str, float]] = {}
    for cell in cells:
        crows = [r for r in rows if r["cell"] == cell.key]
        by_cell[cell.key] = aggregate(crows)
    return {"task": task, "cells": by_cell, "rows": rows, "n_cases": len(cases)}


# ── baseline I/O + regression diff ───────────────────────────────────────────

def save_baseline(report: dict, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"task": report["task"], "cells": report["cells"]}, indent=2), encoding="utf-8")


def load_baseline(path: Path | str) -> dict | None:
    path = Path(path)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def diff(current: dict, baseline: dict | None, *, tol: float = 0.05) -> list[dict]:
    """Per (cell, metric) delta vs baseline. A metric regresses when it moves the
    wrong way by more than ``tol`` (down for higher-better, up for lower-better).
    Cells/metrics absent from the baseline are skipped (nothing to compare)."""
    if not baseline:
        return []
    base_cells = baseline.get("cells", {})
    out: list[dict] = []
    for cell_key, metrics in current.get("cells", {}).items():
        base = base_cells.get(cell_key)
        if not base:
            continue
        for metric, cur in metrics.items():
            if metric not in base:
                continue
            delta = cur - base[metric]
            lower_better = metric in LOWER_BETTER
            regression = (delta > tol) if lower_better else (delta < -tol)
            out.append({"cell": cell_key, "metric": metric,
                        "baseline": base[metric], "current": cur,
                        "delta": delta, "regression": regression})
    return out


def has_regression(diffs: list[dict]) -> bool:
    return any(d["regression"] for d in diffs)


# ── formatting ───────────────────────────────────────────────────────────────

def format_report(report: dict) -> str:
    lines = [f"=== eval-matrix [{report['task']}] ({report['n_cases']} cases) ==="]
    for cell_key, metrics in report["cells"].items():
        m = " ".join(f"{k}={v:.3f}" for k, v in sorted(metrics.items()))
        lines.append(f"  {cell_key}: {m}")
    return "\n".join(lines)


def format_diff(diffs: list[dict]) -> str:
    if not diffs:
        return "  (no baseline to diff against)"
    lines = []
    for d in sorted(diffs, key=lambda x: (not x["regression"], x["cell"])):
        flag = "REGRESSION" if d["regression"] else "ok"
        lines.append(f"  [{flag}] {d['cell']} {d['metric']}: "
                     f"{d['baseline']:.3f} -> {d['current']:.3f} ({d['delta']:+.3f})")
    return "\n".join(lines)


# ── live runners (drive the real pipeline; not used in unit tests) ───────────

@contextmanager
def _pinned(task: str, variant: str):
    """Force the task's prompt variant for the duration of a cell run."""
    reg = get_registry()
    if not variant or variant in ("latest", "base"):
        yield
        return
    prev = get_registry().get_pins()
    reg.set_pins({**prev, task: variant})
    try:
        yield
    finally:
        reg.set_pins(prev)


def _filter_from(case: dict):
    from utils.schema import MetaFilter
    f = case.get("filter")
    return MetaFilter(**f) if f else None


def answer_runner(pipe, *, judge_model: str) -> Callable[[dict, str, str], dict]:
    """Score the ``answer`` task per cell: retrieve → generate (cell model) →
    LLM-judge. Metrics: faithfulness, relevancy, n_details, n_unsupported."""
    from eval.judge import judge_answer
    from llm.generate import generate_answer
    from llm.schema_health import capture_schema

    def run(case: dict, variant: str, model: str) -> dict:
        q = case["question"]
        with _pinned("answer", variant), capture_schema() as sc:
            chunks, _ = pipe.retriever.retrieve(q, base_filter=_filter_from(case))
            ans = generate_answer(pipe.llm, q, chunks, [], model=model)
            # judge on a fixed model, so its schema outcomes don't skew this
            # cell's repair_rate for the model under test.
            j = judge_answer(pipe.llm, q, ans, chunks, [], model=judge_model)
        return {"faithfulness": j["faithfulness"], "relevancy": j["relevancy"],
                "n_details": float(len(ans.details)),
                "n_unsupported": float(len(ans.unsupported)),
                "repair_rate": sc.repair_rate()}
    return run


def synthesize_runner(pipe, *, fields=None,
                      horizon_days=None) -> Callable[[dict, str, str], dict]:
    """Score the ``synthesize`` task per cell: run the query, then re-ground the
    shock vector with the cell model. Metrics: grounded (field count), coverage
    (kept/emitted), n_dropped."""
    def run(case: dict, variant: str, model: str) -> dict:
        q = case["question"]
        with _pinned("synthesize", variant):
            qr = pipe.answer(q, base_filter=_filter_from(case))
            if qr.answer.refused or not qr.chunks:
                return {"grounded": 0.0, "coverage": 0.0, "n_dropped": 0.0, "skipped": 1.0}
            kept, diag = pipe._ground_shock(
                q, qr, model, fields, case.get("horizon_days", horizon_days))
        emitted = len(diag.emitted_fields)
        return {"grounded": float(len(kept)),
                "coverage": (len(kept) / emitted) if emitted else 0.0,
                "n_dropped": float(len(diag.dropped)), "skipped": 0.0}
    return run


# The two runners take different keyword options, so the mapping is deliberately
# loose; the CLI picks one by task name and passes that task's own options.
RUNNERS: dict[str, Callable[..., Any]] = {"answer": answer_runner,
                                          "synthesize": synthesize_runner}
