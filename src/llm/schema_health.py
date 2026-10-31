"""Schema-health observability for structured LLM output (Production Hardening §B / Phase 2).

``complete_json`` validates → repairs → falls back, but that path only ``log.warning``d;
the designed *record → observe* loop was never built, so model drift on structured
output was invisible and the matrix's ``repair_rate`` metric was declared but never
computed. This module is the recorder.

Every structured call reports one outcome per task/model:
  - ``ok``       — first parse satisfied the schema
  - ``repaired`` — the one repair retry fixed it
  - ``failed``   — still invalid; fell back to best-effort parse
  - ``refused``  — the provider refused; no repair was attempted
plus ``truncated`` when a reply was cut off at the output cap (a marker on a call already
counted above, so it is not part of ``total``).

Outcomes feed three sinks: a process-global aggregate (``/admin/schema-health``), the
Prometheus registry (``macrorag_schema_results_total`` — §J), and an optional scoped
capture (``capture_schema``) the eval matrix uses to compute per-cell ``repair_rate``.
"""

from __future__ import annotations

import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass

OK = "ok"
REPAIRED = "repaired"
FAILED = "failed"
REFUSED = "refused"        # the provider refused the request
TRUNCATED = "truncated"    # a reply was cut off at the output cap (marker, not in total)
_OUTCOMES = (OK, REPAIRED, FAILED, REFUSED, TRUNCATED)


@dataclass
class SchemaCounts:
    ok: int = 0
    repaired: int = 0
    failed: int = 0
    refused: int = 0
    truncated: int = 0

    def add(self, outcome: str) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)

    @property
    def total(self) -> int:
        return self.ok + self.repaired + self.failed + self.refused

    def repair_rate(self) -> float:
        """Fraction of calls that did NOT parse cleanly first try (repaired + failed).
        Higher = more structured-output drift. 0.0 when no calls."""
        return (self.repaired + self.failed) / self.total if self.total else 0.0

    def as_dict(self) -> dict:
        return {"ok": self.ok, "repaired": self.repaired, "failed": self.failed,
                "refused": self.refused, "truncated": self.truncated,
                "total": self.total, "repair_rate": round(self.repair_rate(), 4)}


# ── process-global aggregate (admin panel) ───────────────────────────────────
_global: dict[tuple[str, str], SchemaCounts] = {}
_global_lock = threading.Lock()

# ── scoped capture (eval-matrix cell) ────────────────────────────────────────
_capture: contextvars.ContextVar[SchemaCounts | None] = contextvars.ContextVar(
    "schema_capture", default=None)


def record_schema_result(task: str | None, model: str | None, outcome: str) -> None:
    """Record one structured-output outcome. Best-effort — never raises."""
    if outcome not in _OUTCOMES:
        return
    task = task or "-"
    model = model or "-"
    with _global_lock:
        _global.setdefault((task, model), SchemaCounts()).add(outcome)
    cap = _capture.get()
    if cap is not None:
        cap.add(outcome)
    try:  # §J metrics
        from utils.metrics import record_schema_result as _metric
        _metric(task, model, outcome)
    except Exception:  # pragma: no cover - defensive
        pass


@contextmanager
def capture_schema():
    """Scope a fresh :class:`SchemaCounts` covering the structured calls inside the
    block (used by the eval matrix to compute a cell's ``repair_rate``). Nests: an
    inner capture shadows the outer for its duration."""
    counts = SchemaCounts()
    token = _capture.set(counts)
    try:
        yield counts
    finally:
        _capture.reset(token)


def schema_health_snapshot() -> dict:
    """Per task/model outcome counts + repair_rate, for ``/admin/schema-health``."""
    with _global_lock:
        items = list(_global.items())
    by_task: dict[str, dict] = {}
    for (task, model), counts in items:
        by_task.setdefault(task, {})[model] = counts.as_dict()
    return {"by_task": by_task,
            "totals": _totals(items)}


def _totals(items) -> dict:
    agg = SchemaCounts()
    for _, counts in items:
        agg.ok += counts.ok
        agg.repaired += counts.repaired
        agg.failed += counts.failed
        agg.refused += counts.refused
        agg.truncated += counts.truncated
    return agg.as_dict()


def reset_schema_health() -> None:
    """Clear the global aggregate (tests / a fresh observation window)."""
    with _global_lock:
        _global.clear()
