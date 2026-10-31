"""Vector + time-series stores for Macro RAG."""

from __future__ import annotations

from .base import VectorStore
from .filters import match_payload
from .memory import InMemoryVectorStore
from .timeseries_store import TimeseriesStore

__all__ = [
    "VectorStore", "InMemoryVectorStore", "TimeseriesStore", "match_payload",
    "build_qdrant_store",
]


def build_qdrant_store(settings=None, dense_dim: int = 1024):
    """Construct the Qdrant store (deferred import so tests avoid the dependency)."""
    from utils.config import QdrantSettings

    from .qdrant_store import QdrantStore
    return QdrantStore(settings or QdrantSettings(), dense_dim=dense_dim)
