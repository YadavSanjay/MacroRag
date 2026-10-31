"""Maximal Marginal Relevance reselection for diversity.

Off by default (``mmr_lambda = 1.0`` -> pure relevance, like ARag). When < 1 it
trades some relevance for novelty against already-selected chunks. Pure function
over precomputed vectors so it is trivially testable.
"""

from __future__ import annotations

import math


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def mmr_select(query_vec: list[float], candidates: list[tuple[str, list[float]]],
               *, k: int, lambda_: float = 0.7) -> list[str]:
    """Return up to ``k`` candidate ids ordered by MMR.

    ``candidates`` is ``[(id, vector), ...]`` pre-ranked by relevance. With
    ``lambda_ == 1.0`` this is identity (relevance order).
    """
    if lambda_ >= 1.0:
        return [cid for cid, _ in candidates[:k]]
    remaining = list(candidates)
    selected: list[str] = []
    sel_vecs: list[list[float]] = []
    rel = {cid: _cosine(query_vec, v) for cid, v in candidates}
    while remaining and len(selected) < k:
        best_id, best_vec, best_score = None, None, -1e9
        for cid, vec in remaining:
            novelty = max((_cosine(vec, s) for s in sel_vecs), default=0.0)
            score = lambda_ * rel[cid] - (1.0 - lambda_) * novelty
            if score > best_score:
                best_id, best_vec, best_score = cid, vec, score
        if best_id is None or best_vec is None:
            break              # every remaining score was NaN — nothing to pick
        selected.append(best_id)
        sel_vecs.append(best_vec)
        remaining = [(cid, v) for cid, v in remaining if cid != best_id]
    return selected
