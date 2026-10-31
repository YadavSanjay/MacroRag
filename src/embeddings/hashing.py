"""Deterministic, dependency-free embedder for unit tests.

Produces a stable pseudo-random dense vector (seeded by the text hash) plus a
bag-of-words sparse vector (token -> hashed index). Lexical overlap is real, so
hybrid-search and reranking logic can be exercised without torch or downloads.
NOT for production retrieval quality.
"""

from __future__ import annotations

import hashlib
import math
import re

from utils.schema import Embedding, SparseVector

from .base import Embedder

_TOKEN = re.compile(r"[a-z0-9]+")
_SPARSE_SPACE = 2 ** 18


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


class HashingEmbedder(Embedder):
    def __init__(self, dense_dim: int = 64):
        self.dense_dim = dense_dim

    def _dense(self, text: str) -> list[float]:
        # cheap deterministic LCG seeded by the text; L2-normalised
        state = _seed(text) or 1
        vec = []
        for _ in range(self.dense_dim):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            vec.append((state / 0x7FFFFFFF) * 2.0 - 1.0)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _sparse(self, text: str) -> SparseVector:
        counts: dict[int, float] = {}
        for tok in _TOKEN.findall(text.lower()):
            idx = int(hashlib.md5(tok.encode()).hexdigest()[:8], 16) % _SPARSE_SPACE
            counts[idx] = counts.get(idx, 0.0) + 1.0
        indices = sorted(counts)
        return SparseVector(indices=indices, values=[counts[i] for i in indices])

    def _embed(self, texts: list[str]) -> list[Embedding]:
        return [Embedding(dense=self._dense(t), sparse=self._sparse(t)) for t in texts]

    def embed_documents(self, texts: list[str]) -> list[Embedding]:
        return self._embed(texts)

    def embed_queries(self, texts: list[str]) -> list[Embedding]:
        return self._embed(texts)
