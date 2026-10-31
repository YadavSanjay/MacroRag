"""Embeddings: dense+sparse text encoders for hybrid retrieval."""

from __future__ import annotations

from .base import Embedder
from .hashing import HashingEmbedder

__all__ = ["Embedder", "HashingEmbedder", "build_embedder", "select_embedder"]


def build_embedder(settings=None) -> Embedder:
    """Construct the production embedder (bge-m3). Imported lazily so test code
    that only needs :class:`HashingEmbedder` never pulls in torch/FlagEmbedding."""
    from utils.config import EmbeddingSettings

    from .bge_m3 import BGEM3Embedder
    return BGEM3Embedder(settings or EmbeddingSettings())


def select_embedder(settings) -> Embedder:
    """Remote-aware embedder used by BOTH the query and indexing flows: delegate
    to the shared **RagInference** service (``RemoteEmbedder``) whenever a service
    URL resolves — so ingestion-time embedding never loads bge-m3/torch in-process,
    matching retrieval — else fall back to the in-process :func:`build_embedder`.

    ``settings`` is the full :class:`~utils.config.RagSettings`. The remote/local
    decision (env override > ``inference.remote_url`` config default > disabled) is
    shared with retrieval via ``resolve_inference_url``; when remote, the client's
    ``fallback_local`` honours ``inference.fallback_local`` (OFF in the no-torch
    prod image, so a stopped service fails loud rather than importing absent torch)."""
    from retriever.remote_reranker import resolve_inference_url

    remote_url = resolve_inference_url(settings)
    if remote_url:
        from .remote_embedder import RemoteEmbedder
        return RemoteEmbedder(settings.embedding, url=f"{remote_url}/embed",
                              fallback_local=settings.inference.fallback_local)
    return build_embedder(settings.embedding)
