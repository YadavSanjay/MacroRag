"""Client-side embedder that delegates BGE-M3 dense+sparse encoding to the shared
**RagInference** service instead of loading ``BAAI/bge-m3`` (~1GB) in-process.

Set ``RAGINFER_URL`` and MacroRAG's serving processes (MCP / REST) stop loading
their own embedder copy — the model lives once in the RagInference service,
shared with ARag. Implements the :class:`~embeddings.base.Embedder` contract
(``embed_documents`` / ``embed_queries`` / ``embed_query``), rebuilding the
:class:`~utils.schema.Embedding` / :class:`~utils.schema.SparseVector` from the
JSON response so it is a drop-in for the retriever's query embedding.

The service uses the **same ``BAAI/bge-m3`` model** that indexed the corpus, so
the dense (1024-d cosine) and sparse (token-id lexical weights) vectors land in
the identical space — query/corpus stay comparable.

Network failures raise by default; set ``RAGINFER_FALLBACK_LOCAL=1`` to fall back
to an in-process :class:`~embeddings.bge_m3.BGEM3Embedder`.

Wire contract matches RagInference's ``POST /embed``:
``{"texts": [str], "dense": bool, "sparse": bool, "model"?: str}`` ->
``{"embeddings": [{"dense": [float]|null, "sparse": {"indices", "values"}|null}], ...}``
"""

from __future__ import annotations

import os
from typing import List, Optional

import httpx

from retriever.remote_reranker import _fallback_enabled, resolve_endpoint
from utils.config import EmbeddingSettings
from utils.logging import get_logger
from utils.schema import Embedding, SparseVector

from .base import Embedder

log = get_logger(__name__)

_DEFAULT_TIMEOUT_S = float(os.getenv("RAGINFER_TIMEOUT_S", "300"))


class RemoteEmbedder(Embedder):
    """Delegates embedding to the RagInference ``/embed`` endpoint."""

    def __init__(self, settings: EmbeddingSettings | None = None,
                 url: str | None = None,
                 timeout_s: float = _DEFAULT_TIMEOUT_S,
                 model: str | None = None,
                 fallback_local: bool | None = None):
        self.settings = settings or EmbeddingSettings()
        self.dense_dim = self.settings.dense_dim
        self.url = resolve_endpoint("/embed", url)
        if not self.url:
            raise ValueError(
                "RemoteEmbedder needs RAGINFER_URL (or an explicit url=...)")
        self.timeout_s = timeout_s
        self.model = model or os.getenv("RAGINFER_EMBED_MODEL") or None
        # None -> honour the RAGINFER_FALLBACK_LOCAL env var; else use the caller's
        # value (e.g. the inference.fallback_local config default).
        self._fallback = _fallback_enabled() if fallback_local is None \
            else bool(fallback_local)
        self._local: Embedder | None = None
        log.info("RemoteEmbedder -> %s (timeout=%.0fs, model=%s, fallback_local=%s)",
                 self.url, self.timeout_s, self.model, self._fallback)

    # ── wire call ────────────────────────────────────────────────────────────
    def _encode(self, texts: list[str]) -> list[Embedding]:
        if not texts:
            return []
        payload: dict = {"texts": list(texts), "dense": True, "sparse": True}
        if self.model:
            payload["model"] = self.model
        try:
            resp = httpx.post(self.url, json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            items = resp.json()["embeddings"]
            if len(items) != len(texts):
                raise ValueError(
                    f"service returned {len(items)} embeddings for {len(texts)} texts")
        except Exception:
            if self._fallback:
                log.exception("RemoteEmbedder: service call failed, "
                              "falling back to local BGEM3Embedder")
                # bge-m3 makes no query/document distinction, so either path is
                # equivalent — use the public contract method.
                return self._local_embedder().embed_documents(texts)
            log.error("RemoteEmbedder: service call to %s failed", self.url)
            raise
        return [self._to_embedding(it) for it in items]

    @staticmethod
    def _to_embedding(item: dict) -> Embedding:
        s = item.get("sparse")
        sparse = None
        if s is not None:
            sparse = SparseVector(indices=[int(i) for i in s["indices"]],
                                  values=[float(v) for v in s["values"]])
        return Embedding(dense=[float(x) for x in (item.get("dense") or [])],
                         sparse=sparse)

    # ── Embedder contract ────────────────────────────────────────────────────
    def embed_documents(self, texts: list[str]) -> list[Embedding]:
        return self._encode(texts)

    def embed_queries(self, texts: list[str]) -> list[Embedding]:
        return self._encode(texts)

    def _local_embedder(self) -> Embedder:
        if self._local is None:
            from .bge_m3 import BGEM3Embedder
            self._local = BGEM3Embedder(self.settings)
        return self._local
