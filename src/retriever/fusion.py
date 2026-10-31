"""Reciprocal-rank fusion across retrieval branches (paraphrase / subqueries /
HyDE) and chunk dedup. Within a branch the store already RRF-fuses dense+sparse;
this fuses *across* branches into one ranking."""

from __future__ import annotations

from collections.abc import Iterable

from utils.schema import RetrievedChunk

RRF_K = 60


def reciprocal_rank_fusion(branches: Iterable[list[RetrievedChunk]],
                           *, k: int = RRF_K) -> list[RetrievedChunk]:
    """Fuse several ranked chunk lists. A chunk's fused score is the sum over
    branches of ``1 / (k + rank)``; the highest in-branch score is kept on the
    surviving record for reranker tie-breaks / debugging."""
    fused: dict[str, float] = {}
    best: dict[str, RetrievedChunk] = {}
    for branch in branches:
        for rank, chunk in enumerate(branch):
            fused[chunk.chunk_id] = fused.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            if chunk.chunk_id not in best or chunk.score > best[chunk.chunk_id].score:
                best[chunk.chunk_id] = chunk
    ordered = sorted(fused, key=lambda cid: fused[cid], reverse=True)
    out = []
    for cid in ordered:
        c = best[cid]
        out.append(RetrievedChunk(chunk_id=c.chunk_id, text=c.text, score=fused[cid],
                                  payload=c.payload, rerank_score=c.rerank_score))
    return out


def dedup_by_chunk(chunks: Iterable[RetrievedChunk]) -> list[RetrievedChunk]:
    """Keep the highest-scoring instance of each chunk_id, preserving order."""
    best: dict[str, RetrievedChunk] = {}
    for c in chunks:
        if c.chunk_id not in best or c.score > best[c.chunk_id].score:
            best[c.chunk_id] = c
    return sorted(best.values(), key=lambda c: c.score, reverse=True)
