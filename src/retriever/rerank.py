"""Cross-encoder reranking. Production uses BAAI/bge-reranker-v2-m3; the identity
reranker (relevance passthrough) is the test/no-op default."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from utils.config import RerankerSettings
from utils.logging import get_logger
from utils.schema import RetrievedChunk

log = get_logger(__name__)


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, chunks: list[RetrievedChunk],
               *, top_n: int | None = None) -> list[RetrievedChunk]:
        ...


class IdentityReranker(Reranker):
    """Keeps the incoming (fused) order; copies score into rerank_score."""

    def rerank(self, query, chunks, *, top_n=None):
        for c in chunks:
            c.rerank_score = c.score
        out = sorted(chunks, key=lambda c: c.rerank_score, reverse=True)
        return out[:top_n] if top_n else out


class BGEReranker(Reranker):
    def __init__(self, settings: RerankerSettings | None = None):
        self.settings = settings or RerankerSettings()
        self._model = None

    def _ensure(self):
        if self._model is None:
            from FlagEmbedding import FlagReranker
            log.info("loading reranker %s", self.settings.model)
            self._model = FlagReranker(self.settings.model, use_fp16=True,
                                       device=self.settings.device)
        return self._model

    def rerank(self, query, chunks, *, top_n=None):
        if not chunks:
            return []
        model = self._ensure()
        scores = model.compute_score([[query, c.text] for c in chunks], normalize=True)
        if not isinstance(scores, list):
            scores = [scores]
        for c, s in zip(chunks, scores):
            c.rerank_score = float(s)
        out = sorted(chunks, key=lambda c: c.rerank_score, reverse=True)
        return out[:top_n] if top_n else out
