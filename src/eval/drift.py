"""Drift detection against a saved baseline (ARag review §2.1 Loop C). Compares a
fresh eval summary to a stored baseline and flags any metric that regressed
beyond tolerance — the gate to run before swapping in a re-indexed store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def detect_drift(current: dict[str, float], baseline: dict[str, float],
                 *, tolerance: float = 0.05) -> list[dict]:
    """Return one record per metric that dropped more than ``tolerance`` below
    its baseline (higher-is-better metrics)."""
    regressions = []
    for metric, base in baseline.items():
        cur = current.get(metric)
        if cur is None:
            continue
        if cur < base - tolerance:
            regressions.append({"metric": metric, "baseline": round(base, 4),
                                "current": round(cur, 4),
                                "delta": round(cur - base, 4)})
    return regressions


def save_baseline(path: Path | str, summary: dict[str, float]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def load_baseline(path: Path | str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):  # pragma: no cover
        return None
