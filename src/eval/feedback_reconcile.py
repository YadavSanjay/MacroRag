"""Feedback → golden-set reconciliation (Production Hardening §B).

``POST /feedback`` only appended to ``feedback.jsonl``; nothing ever turned it into
golden cases, so the advertised "grows the golden set" loop did not exist. This
closes it as a **review-gated** step: positive feedback that cites chunks becomes a
*candidate* golden entry (deduped against the live golden set), written to a
separate candidates file for a human to promote — never straight into the golden
set (a thumbs-up is a weak label; auto-promotion would let noise poison eval).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from utils.logging import get_logger

log = get_logger(__name__)


def _read_feedback(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _existing_questions(golden_path: Path) -> set[str]:
    if not golden_path.exists():
        return set()
    data = yaml.safe_load(golden_path.read_text(encoding="utf-8")) or {}
    return {q.get("question", "").strip() for q in data.get("questions", [])}


def reconcile_feedback(feedback_path: Path | str, golden_path: Path | str,
                       *, min_rating: int = 1) -> list[dict[str, Any]]:
    """Propose golden entries from positive, chunk-citing feedback.

    A candidate uses ``match_field: chunk_id`` with the upvoted ``chunk_ids`` as the
    gold set (a valid golden shape — chunk_id is a payload key). Skips feedback whose
    question is already in the golden set, and de-dups repeated questions (keeping the
    first). Returns candidates *only* — writing to golden is a human decision.
    """
    feedback_path, golden_path = Path(feedback_path), Path(golden_path)
    existing = _existing_questions(golden_path)
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for rec in _read_feedback(feedback_path):
        question = str(rec.get("question", "")).strip()
        chunk_ids = rec.get("chunk_ids") or []
        if not question or not chunk_ids:
            continue
        if int(rec.get("rating", 0)) < min_rating:
            continue
        if question in existing or question in seen:
            continue
        seen.add(question)
        candidates.append({
            "question": question,
            "match_field": "chunk_id",
            "expected": list(chunk_ids),
            "source": "feedback",          # provenance; strip before promoting
            "ts": rec.get("ts"),
        })
    return candidates


def write_candidates(candidates: list[dict[str, Any]], path: Path | str) -> Path:
    """Write candidate golden entries to a YAML review file (``questions:`` shape,
    same as golden). Overwrites — it is a regenerated proposal, not an append log."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"questions": candidates}, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    log.info("wrote %d golden candidate(s) -> %s", len(candidates), path)
    return path
