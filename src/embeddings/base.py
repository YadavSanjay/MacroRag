"""Embedder interface. One model produces both a dense vector and sparse lexical
weights per text (the bge-m3 trick), so the vector store can run hybrid search."""

from __future__ import annotations

from abc import ABC, abstractmethod

from utils.schema import Embedding


class Embedder(ABC):
    """Dense + sparse text embedder."""

    dense_dim: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[Embedding]:
        """Embed corpus texts (index time)."""

    @abstractmethod
    def embed_queries(self, texts: list[str]) -> list[Embedding]:
        """Embed query texts (search time). Often identical to documents, but
        kept separate so instruction-tuned models can use a query prefix."""

    def embed_query(self, text: str) -> Embedding:
        return self.embed_queries([text])[0]
