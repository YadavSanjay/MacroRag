"""Pure-python in-memory hybrid store.

Mirrors the Qdrant adapter's contract (dense cosine + sparse dot, RRF-fused,
facet-filtered) without a server — used by unit tests and small local runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from utils.schema import ChunkPoint, Embedding, MetaFilter, RetrievedChunk, SparseVector

from .base import VectorStore
from .filters import match_payload

_RRF_K = 60


@dataclass
class _Stored:
    chunk_id: str
    text: str
    payload: dict[str, Any]
    dense: list[float]
    sparse: SparseVector | None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _sparse_dot(a: SparseVector | None, b: SparseVector | None) -> float:
    if not a or not b:
        return 0.0
    bmap = dict(zip(b.indices, b.values))
    return sum(v * bmap.get(i, 0.0) for i, v in zip(a.indices, a.values))


def _rrf(ranking: list[str]) -> dict[str, float]:
    return {cid: 1.0 / (_RRF_K + rank) for rank, cid in enumerate(ranking)}


class InMemoryVectorStore(VectorStore):
    def __init__(self):
        self._items: dict[str, _Stored] = {}

    def ensure_collection(self, *, recreate: bool = False) -> None:
        if recreate:
            self._items.clear()

    def upsert(self, points: list[ChunkPoint], embeddings: list[Embedding]) -> int:
        for p, e in zip(points, embeddings):
            self._items[p.chunk_id] = _Stored(p.chunk_id, p.text, p.payload,
                                              e.dense, e.sparse)
        return len(points)

    def count(self) -> int:
        return len(self._items)

    def search(self, query: Embedding, *, limit: int,
               filt: MetaFilter | None = None) -> list[RetrievedChunk]:
        cands = [s for s in self._items.values()
                 if filt is None or filt.is_empty() or match_payload(filt, s.payload)]
        if not cands:
            return []
        dense_rank = sorted(cands, key=lambda s: _cosine(query.dense, s.dense), reverse=True)
        sparse_rank = sorted(cands, key=lambda s: _sparse_dot(query.sparse, s.sparse), reverse=True)
        fused = _rrf([s.chunk_id for s in dense_rank])
        for cid, sc in _rrf([s.chunk_id for s in sparse_rank]).items():
            fused[cid] = fused.get(cid, 0.0) + sc
        top = sorted(cands, key=lambda s: fused[s.chunk_id], reverse=True)[:limit]
        return [RetrievedChunk(chunk_id=s.chunk_id, text=s.text,
                               score=fused[s.chunk_id], payload=s.payload) for s in top]

    def get_by_chunk_ids(self, chunk_ids: list[str]) -> list[RetrievedChunk]:
        out = []
        for cid in chunk_ids:
            s = self._items.get(cid)
            if s:
                out.append(RetrievedChunk(chunk_id=s.chunk_id, text=s.text,
                                          score=0.0, payload=s.payload))
        return out

    def existing_signatures(self) -> dict[str, str]:
        return {cid: str(s.payload.get("content_hash", ""))
                for cid, s in self._items.items()}

    def delete_by_chunk_ids(self, chunk_ids: list[str]) -> int:
        for cid in chunk_ids:
            self._items.pop(cid, None)
        return len(chunk_ids)
