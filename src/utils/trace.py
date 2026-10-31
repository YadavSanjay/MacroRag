"""Structured per-query trace -> JSONL. The seed of observability: one line per
query capturing route, extracted filters, retrieved chunk ids + scores, rerank
scores, hydrated series, refusal/verification outcome, timings, and (if the LLM
client exposes it) token usage. Best-effort — tracing never breaks a query."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .logging import get_logger

log = get_logger(__name__)


class Tracer:
    def __init__(self, path: Path | str, *, enabled: bool = True):
        self.path = Path(path)
        self.enabled = enabled

    def write(self, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:  # pragma: no cover - disk issues shouldn't fail a query
            log.warning("trace write failed: %s", exc)


def _answer_view(answer) -> dict[str, Any]:
    """The full generated answer as stored in the trace: the prose summary, each
    cited detail, and the claims the verifier dropped / the query left open."""
    return {
        "summary": answer.summary,
        "details": [{"text": d.text, "chunk_ids": d.chunk_ids}
                    for d in answer.details],
        "unanswered": list(answer.unanswered),
        "unsupported": list(answer.unsupported),
        "used_chunk_ids": list(answer.used_chunk_ids),
        "refused": answer.refused,
    }


def build_trace(query: str, analysis, chunks, answer, *,
                timings: dict[str, float] | None = None,
                usage: dict | None = None,
                escalations: list | None = None,
                security: list | None = None) -> dict[str, Any]:
    f = analysis.filters
    return {
        "ts": datetime.now(UTC).isoformat(),
        "query": query,
        # §D: suspected prompt-injection markers found in untrusted input (None = clean)
        "security": {"injection_markers": security} if security else None,
        "route": analysis.route,
        "filters": {
            "factor_tags": f.factor_tags, "entity_tags": f.entity_tags,
            "tiers": f.tiers, "doc_types": f.doc_types,
            "date_from": f.date_from, "date_to": f.date_to,
        },
        "n_branches": len(analysis.search_queries()),
        "retrieved": [
            {"chunk_id": c.chunk_id, "source_id": c.payload.get("source_id"),
             "score": round(c.score, 5),
             "rerank_score": round(c.rerank_score, 5) if c.rerank_score is not None else None,
             "expanded": bool(c.payload.get("expanded"))}
            for c in chunks
        ],
        "timeseries": [h.source for h in answer.timeseries],
        "refused": answer.refused,
        "n_details": len(answer.details),
        "n_unsupported": len(answer.unsupported),
        "answer": _answer_view(answer),
        "timings_ms": {k: round(v * 1000, 1) for k, v in (timings or {}).items()},
        "usage": usage,
        # Model-escalation events for this query (quality-signal driven, §5). Absent
        # when escalation is disabled or nothing escalated.
        "escalations": escalations or None,
    }


def build_synthesis_trace(query: str, diag, *, world_view_id: str | None = None,
                          usage: dict | None = None,
                          escalations: list | None = None) -> dict[str, Any]:
    """One line per synthesize call: the shock-vector grounding outcome + why each
    field did/didn't ground (see synthesis.diagnostics.SynthesisDiagnostics)."""
    return {
        "ts": datetime.now(UTC).isoformat(),
        "kind": "synthesize",
        "query": query,
        "world_view_id": world_view_id,
        **diag.as_dict(),
        "usage": usage,
        "escalations": escalations or None,
    }


class Timer:
    """Tiny context-managed stopwatch: ``with Timer() as t: ...; t.elapsed``."""

    def __enter__(self):
        self._t0 = time.perf_counter()
        self.elapsed = 0.0
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self._t0
        return False
