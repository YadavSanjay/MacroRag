"""Vector-store interface. Implementations provide hybrid (dense+sparse) search
with server-side or local RRF fusion, MacroRAG facet filtering, and lookup by
chunk_id (used for prev/next chunk expansion)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from utils.schema import ChunkPoint, Embedding, MetaFilter, RetrievedChunk


class VectorStore(ABC):
    @abstractmethod
    def ensure_collection(self, *, recreate: bool = False) -> None:
        ...

    @abstractmethod
    def upsert(self, points: list[ChunkPoint], embeddings: list[Embedding]) -> int:
        """Index points with their precomputed embeddings; returns count."""

    @abstractmethod
    def search(self, query: Embedding, *, limit: int,
               filt: MetaFilter | None = None) -> list[RetrievedChunk]:
        """Hybrid dense+sparse retrieval, RRF-fused, facet-filtered."""

    @abstractmethod
    def get_by_chunk_ids(self, chunk_ids: list[str]) -> list[RetrievedChunk]:
        """Fetch stored chunks by chunk_id (for neighbour expansion)."""

    @abstractmethod
    def count(self) -> int:
        ...

    @abstractmethod
    def existing_signatures(self) -> dict[str, str]:
        """Map ``chunk_id -> content_hash`` for every stored point (empty string
        when a point carries no ``content_hash``). Backs incremental indexing:
        the diff embeds only chunks whose id is new or whose hash changed."""

    @abstractmethod
    def delete_by_chunk_ids(self, chunk_ids: list[str]) -> int:
        """Delete stored points by chunk_id; returns the number requested. Used by
        incremental indexing to drop chunks no longer present in the corpus."""
