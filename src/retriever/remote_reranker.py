"""Client-side reranker that delegates scoring to the shared **RagInference**
service instead of loading ``bge-reranker-v2-m3`` in-process.

Set ``RAGINFER_URL`` (e.g. ``http://127.0.0.1:8799``) and MacroRAG stops loading
its own ~1GB reranker copy — the model lives once in the RagInference service and
is shared with ARag (both MCP subprocesses serving Faultline's scenariogen).
Implements the same :class:`~retriever.rerank.Reranker` contract
(``rerank(query, chunks, *, top_n) -> list[RetrievedChunk]``), so it drops into
the existing retrieval path.

Network failures raise by default (the service is the intended single owner of
the model); set ``RAGINFER_FALLBACK_LOCAL=1`` to fall back to an in-process
:class:`~retriever.rerank.BGEReranker` if the service is unreachable.

The wire contract matches RagInference's ``POST /rerank``:
``{"query": str, "documents": [str], "model"?: str} -> {"scores": [float], ...}``
— scores aligned to input document order.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

from utils.logging import get_logger
from utils.schema import RetrievedChunk

from .rerank import Reranker

log = get_logger(__name__)

# Rerank of ~30 long docs on CPU can take ~90s; keep the client patient.
_DEFAULT_TIMEOUT_S = float(os.getenv("RAGINFER_TIMEOUT_S", "300"))


def remote_inference_url() -> str:
    """The configured RagInference base URL (``RAGINFER_URL``), or '' if unset."""
    return os.getenv("RAGINFER_URL", "").strip().rstrip("/")


def remote_inference_enabled() -> bool:
    """True when retrieval should use the shared RagInference service."""
    return bool(remote_inference_url())


def resolve_endpoint(path: str, explicit: str | None = None) -> str:
    """Resolve a service endpoint: an explicit full URL wins, else
    ``RAGINFER_URL`` base + *path* (e.g. '/rerank')."""
    if explicit:
        return explicit
    base = remote_inference_url()
    return f"{base}{path}" if base else ""


def resolve_inference_url(settings) -> str:
    """The shared-inference base URL to use, applying the env override over the
    committed config default. If ``RAGINFER_URL`` is present in the environment it
    wins — a non-empty value points elsewhere, an empty value forces in-process
    models; otherwise the ``inference.remote_url`` config default applies. Returns
    ``''`` when remote inference is disabled (use local models).

    Shared by both the query flow (``build_retriever``) and the indexing flow
    (``select_embedder``) so ingestion-time embedding routes to RagInference the
    same way retrieval does."""
    if "RAGINFER_URL" in os.environ:
        return remote_inference_url()               # '' when explicitly emptied
    return (settings.inference.remote_url or "").strip().rstrip("/")


def _fallback_enabled() -> bool:
    return os.getenv("RAGINFER_FALLBACK_LOCAL", "0").strip().lower() in (
        "1", "true", "yes", "on")


class RemoteReranker(Reranker):
    """Delegates ``rerank`` to the RagInference ``/rerank`` endpoint."""

    def __init__(self, url: str | None = None,
                 timeout_s: float = _DEFAULT_TIMEOUT_S,
                 model: str | None = None,
                 fallback_local: bool | None = None):
        self.url = resolve_endpoint("/rerank", url)
        if not self.url:
            raise ValueError(
                "RemoteReranker needs RAGINFER_URL (or an explicit url=...)")
        self.timeout_s = timeout_s
        self.model = model or os.getenv("RAGINFER_RERANK_MODEL") or None
        # None -> honour the RAGINFER_FALLBACK_LOCAL env var; else use the caller's
        # value (e.g. the inference.fallback_local config default).
        self._fallback = _fallback_enabled() if fallback_local is None \
            else bool(fallback_local)
        self._local: Reranker | None = None
        log.info("RemoteReranker -> %s (timeout=%.0fs, model=%s, fallback_local=%s)",
                 self.url, self.timeout_s, self.model, self._fallback)

    def rerank(self, query: str, chunks: list[RetrievedChunk], *,
               top_n: int | None = None) -> list[RetrievedChunk]:
        if not chunks:
            return []
        payload: dict = {"query": query, "documents": [c.text for c in chunks]}
        if self.model:
            payload["model"] = self.model
        try:
            resp = httpx.post(self.url, json=payload, timeout=self.timeout_s)
            resp.raise_for_status()
            scores = resp.json()["scores"]
            if len(scores) != len(chunks):
                raise ValueError(
                    f"service returned {len(scores)} scores for {len(chunks)} docs")
        except Exception:
            if self._fallback:
                log.exception("RemoteReranker: service call failed, "
                              "falling back to local BGEReranker")
                return self._local_reranker().rerank(query, chunks, top_n=top_n)
            log.error("RemoteReranker: service call to %s failed", self.url)
            raise
        for c, s in zip(chunks, scores):
            c.rerank_score = float(s)
        # The length check above guarantees every chunk was scored, so the
        # fallback never fires; it keeps the sort total if that ever changes
        # (`or 0.0` is identity for any real score, including a negative one).
        out = sorted(chunks, key=lambda c: c.rerank_score or 0.0, reverse=True)
        return out[:top_n] if top_n else out

    def _local_reranker(self) -> Reranker:
        if self._local is None:
            from .rerank import BGEReranker
            self._local = BGEReranker()
        return self._local
