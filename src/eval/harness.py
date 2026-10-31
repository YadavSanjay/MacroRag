"""Evaluation harness. Runs retrieval over a golden set and reports hit@k / MRR /
recall@k by a declared match field. Retrieval-only by default (cheap, offline);
pass an LLM to also generate answers for manual/LLM-judge review.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import yaml

from retriever.retriever import Retriever
from utils.logging import get_logger
from utils.schema import MetaFilter

from . import metrics

log = get_logger(__name__)


def load_golden(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return data.get("questions", [])


def _filter_from(item: dict) -> MetaFilter | None:
    f = item.get("filter")
    return MetaFilter(**f) if f else None


def evaluate_retrieval(retriever: Retriever, golden: list[dict[str, Any]],
                       *, k: int = 8,
                       should_cancel: Callable[[], None] | None = None) -> dict[str, Any]:
    """Score each question's retrieval against its gold key set.

    ``should_cancel`` (optional) is called at each question boundary; it may raise
    to abort a long eval cooperatively (used by the admin job's cancel button)."""
    per_query = []
    rows = []
    for item in golden:
        if should_cancel is not None:
            should_cancel()
        field = item.get("match_field", "source_id")
        gold = item.get("expected", [])
        chunks, _ = retriever.retrieve(item["question"], base_filter=_filter_from(item))
        keys = metrics.projected_keys(chunks, field)
        m = {
            f"hit@{k}": metrics.hit_at_k(keys, gold, k),
            f"recall@{k}": metrics.recall_at_k(keys, gold, k),
            "mrr": metrics.reciprocal_rank(keys, gold),
        }
        per_query.append(m)
        rows.append({"question": item["question"], "gold": gold,
                     "top": keys[:k], **m})
    return {"summary": metrics.aggregate(per_query), "rows": rows}


def evaluate_answers(pipe, golden: list[dict[str, Any]], *,
                     judge_model: str | None = None,
                     should_cancel: Callable[[], None] | None = None) -> dict[str, Any]:
    """Aggregate *answer quality* over the golden set (§B): for each question,
    retrieve → generate → LLM-judge, then mean faithfulness/relevancy. This is the
    system-wide faithfulness the eval job/UI never measured (only per-answer
    confidence was shown). Needs a full pipeline (retriever + llm); ``judge_model``
    pins the judge tier (defaults to the client default)."""
    from llm.generate import generate_answer

    from .judge import judge_answer

    rows, faith, rel = [], [], []
    for item in golden:
        if should_cancel is not None:
            should_cancel()
        q = item["question"]
        chunks, _ = pipe.retriever.retrieve(q, base_filter=_filter_from(item))
        ans = generate_answer(pipe.llm, q, chunks, [])
        j = judge_answer(pipe.llm, q, ans, chunks, [], model=judge_model)
        faith.append(j["faithfulness"])
        rel.append(j["relevancy"])
        rows.append({"question": q, "faithfulness": j["faithfulness"],
                     "relevancy": j["relevancy"], "reason": j["reason"]})

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {"summary": {"faithfulness": _mean(faith), "relevancy": _mean(rel)},
            "rows": rows}
