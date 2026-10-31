"""Bucket-aware time-decay re-scoring (ARag review §13.5 P2 / "latest supersedes").

After cross-encoder reranking, optionally fold a recency boost into the score so
fresher releases outrank stale ones — modulated per bucket: catalysts (A) and
periodic forecasts (B) are recency-sensitive; rolling commentary (C) less so. A
pure function over the chunk's ``release_date`` payload; off by default.
"""

from __future__ import annotations

import math
from datetime import date

from utils.config import RankingSettings
from utils.schema import RetrievedChunk


def _age_days(release_date: str | None, today: date) -> float | None:
    if not release_date:
        return None
    try:
        d = date.fromisoformat(str(release_date)[:10])
    except ValueError:
        return None
    return max(0.0, (today - d).days)


def apply_time_decay(chunks: list[RetrievedChunk], settings: RankingSettings,
                     *, today: date | None = None) -> list[RetrievedChunk]:
    """Re-score by ``base * (decay ** (age / half_life))`` and re-sort.

    ``decay`` per bucket scales how aggressively age discounts the score
    (1.0 = full half-life decay, 0.0 = no decay). Chunks with no date keep their
    base score. Returns the chunks re-ordered (rerank_score updated in place).
    """
    if not settings.time_decay or not chunks:
        return chunks
    today = today or date.today()
    half_life = max(settings.half_life_days, 1.0)
    for c in chunks:
        base = c.rerank_score if c.rerank_score is not None else c.score
        age = _age_days(c.payload.get("release_date"), today)
        if age is None:
            continue
        bucket = c.payload.get("bucket", "C")
        sensitivity = settings.bucket_decay.get(bucket, 0.5)
        factor = math.pow(0.5, sensitivity * age / half_life)  # 1.0..0.0
        c.rerank_score = base * factor
    chunks.sort(key=lambda c: (c.rerank_score if c.rerank_score is not None else c.score),
                reverse=True)
    return chunks
