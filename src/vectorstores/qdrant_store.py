"""Qdrant hybrid vector store (the ARag-style production backend).

Named vectors ``dense`` (cosine) + ``sparse`` in one collection; search uses the
native Query API with ``FusionQuery(RRF)`` (server-side prefetch-and-fuse).
MacroRAG facets become a payload ``Filter``; ``release_dt`` is indexed as a
datetime for range queries. Bulk ingest sets ``hnsw m=0`` then resets to 16 so
the graph is built once after load.
"""

from __future__ import annotations

from typing import Any, Optional

from utils.config import QdrantSettings
from utils.logging import get_logger
from utils.schema import ChunkPoint, Embedding, MetaFilter, RetrievedChunk
from utils.text import point_uuid

from .base import VectorStore

log = get_logger(__name__)


class QdrantStore(VectorStore):
    def __init__(self, settings: QdrantSettings, dense_dim: int = 1024,
                 *, path: str | None = None):
        from qdrant_client import QdrantClient

        self.s = settings
        self.dense_dim = dense_dim
        if settings.url:
            self.client = QdrantClient(**self._server_kwargs(settings))
        elif settings.location:
            self.client = QdrantClient(location=settings.location)
        else:
            self.client = QdrantClient(path=path or settings.path)

    @staticmethod
    def _server_kwargs(settings: QdrantSettings) -> dict[str, Any]:
        """Client kwargs for server mode. TLS/api-key/timeout are added only when
        configured, so an in-VPC plaintext server needs none of them; the API key
        is resolved as a secret (``<NAME>_FILE`` mount aware)."""
        from utils.config import read_secret

        kwargs: dict[str, Any] = {"url": settings.url}
        if settings.api_key_env:
            key = read_secret(settings.api_key_env)
            if key:
                kwargs["api_key"] = key
        if settings.https is not None:
            kwargs["https"] = settings.https
        if settings.verify is not None:
            kwargs["verify"] = settings.verify
            # A self-signed server (verify off) also trips the client's start-up
            # version-compatibility probe: it runs in a background thread that
            # ignores `verify`, so it fails on the cert and logs a misleading
            # "failed to obtain server version" warning. Skip it in that case.
            if settings.verify is False:
                kwargs["check_compatibility"] = False
        if settings.timeout is not None:
            kwargs["timeout"] = settings.timeout
        if settings.prefer_grpc:
            kwargs["prefer_grpc"] = True
        return kwargs

    # ── collection lifecycle ─────────────────────────────────────────────────

    def ensure_collection(self, *, recreate: bool = False) -> None:
        from qdrant_client import models

        exists = self.client.collection_exists(self.s.collection)
        if exists and not recreate:
            return
        if exists:
            self.client.delete_collection(self.s.collection)
        quant = None
        if self.s.quantization:        # int8 scalar: ~4x memory cut, ~1% recall hit
            quant = models.ScalarQuantization(scalar=models.ScalarQuantizationConfig(
                type=models.ScalarType.INT8, always_ram=True))
        self.client.create_collection(
            collection_name=self.s.collection,
            vectors_config={self.s.dense_vector: models.VectorParams(
                size=self.dense_dim, distance=models.Distance.COSINE, on_disk=True)},
            sparse_vectors_config={self.s.sparse_vector: models.SparseVectorParams(
                index=models.SparseIndexParams(on_disk=self.s.sparse_on_disk))},
            # build the HNSW graph once, after bulk load
            hnsw_config=models.HnswConfigDiff(m=0),
            quantization_config=quant,
        )
        # Payload indexes are a server-side optimization (no effect / warning in
        # local/on-disk mode, where filtering runs in-process anyway).
        if self.s.url:
            for field, schema in (("tier", "integer"), ("bucket", "keyword"),
                                  ("doc_type", "keyword"), ("publisher", "keyword"),
                                  ("entity_tags", "keyword"), ("factor_tags", "keyword"),
                                  ("doc_id", "keyword"), ("chunk_id", "keyword"),
                                  ("tenant_id", "keyword")):
                self.client.create_payload_index(self.s.collection, field, schema)
            self.client.create_payload_index(self.s.collection, "release_dt",
                                              models.PayloadSchemaType.DATETIME)

    def finalize_index(self) -> None:
        """Rebuild the HNSW graph after bulk ingest (m=0 -> 16)."""
        from qdrant_client import models
        self.client.update_collection(
            self.s.collection, hnsw_config=models.HnswConfigDiff(m=16))

    # ── ingest ───────────────────────────────────────────────────────────────

    def upsert(self, points: list[ChunkPoint], embeddings: list[Embedding]) -> int:
        from qdrant_client import models

        records = []
        for p, e in zip(points, embeddings):
            payload = dict(p.payload)
            payload["text"] = p.text
            rel = payload.get("release_date")
            if rel:
                payload["release_dt"] = f"{rel}T00:00:00Z"
            vec: dict[str, Any] = {self.s.dense_vector: e.dense}
            if e.sparse:
                vec[self.s.sparse_vector] = models.SparseVector(
                    indices=e.sparse.indices, values=e.sparse.values)
            records.append(models.PointStruct(id=p.point_id, vector=vec, payload=payload))
        self.client.upsert(self.s.collection, points=records)
        return len(records)

    # ── search ───────────────────────────────────────────────────────────────

    def _build_filter(self, mf: MetaFilter | None):
        from qdrant_client import models

        if mf is None or mf.is_empty():
            return None
        must = []
        for field, vals in (("tier", mf.tiers), ("bucket", mf.buckets),
                            ("doc_type", mf.doc_types), ("publisher", mf.publishers),
                            ("entity_tags", mf.entity_tags), ("factor_tags", mf.factor_tags)):
            if not vals:
                continue
            cond = models.FieldCondition(key=field, match=models.MatchAny(any=list(vals)))
            if field == "entity_tags" and mf.entity_exempt_factor_tags:
                # entity match OR a scenario-study factor tag (see MetaFilter)
                must.append(models.Filter(should=[cond, models.FieldCondition(
                    key="factor_tags",
                    match=models.MatchAny(any=list(mf.entity_exempt_factor_tags)))]))
            else:
                must.append(cond)
        if mf.date_from or mf.date_to:
            must.append(models.FieldCondition(key="release_dt", range=models.DatetimeRange(
                gte=f"{mf.date_from}T00:00:00Z" if mf.date_from else None,
                lte=f"{mf.date_to}T23:59:59Z" if mf.date_to else None)))
        if mf.tenant_id:
            # Dormant tenant hook: this tenant OR the shared corpus (no tenant_id
            # on the payload). No ingestion path writes tenant_id today, so every
            # point is shared and this filter is a no-op — retained so retrieval
            # discriminates correctly if a private per-tenant ingest path is ever
            # added. What the API stamps here is the caller's **principal** — an
            # auth/rate-limit/cost identity today, not a data-isolation boundary.
            # See docs/design/Macro_RAG_Production_Hardening.md §A.
            must.append(models.Filter(should=[
                models.FieldCondition(key="tenant_id",
                                      match=models.MatchValue(value=mf.tenant_id)),
                models.IsEmptyCondition(is_empty=models.PayloadField(key="tenant_id")),
            ]))
        return models.Filter(must=must) if must else None

    def search(self, query: Embedding, *, limit: int,
               filt: MetaFilter | None = None) -> list[RetrievedChunk]:
        from qdrant_client import models

        qfilter = self._build_filter(filt)
        prefetch = [models.Prefetch(query=query.dense, using=self.s.dense_vector,
                                    limit=limit * 2, filter=qfilter)]
        if query.sparse:
            prefetch.append(models.Prefetch(
                query=models.SparseVector(indices=query.sparse.indices,
                                          values=query.sparse.values),
                using=self.s.sparse_vector, limit=limit * 2, filter=qfilter))
        res = self.client.query_points(
            self.s.collection, prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit, with_payload=True)
        return [self._to_chunk(p) for p in res.points]

    def get_by_chunk_ids(self, chunk_ids: list[str]) -> list[RetrievedChunk]:
        ids = [point_uuid(c) for c in chunk_ids]
        recs = self.client.retrieve(self.s.collection, ids=ids, with_payload=True)
        return [self._to_chunk(r) for r in recs]

    def count(self) -> int:
        return self.client.count(self.s.collection).count

    def existing_signatures(self) -> dict[str, str]:
        """Scroll the whole collection, projecting each point to
        ``chunk_id -> content_hash`` (payload-only read, no vectors)."""
        if not self.client.collection_exists(self.s.collection):
            return {}
        out: dict[str, str] = {}
        offset = None
        while True:
            recs, offset = self.client.scroll(
                self.s.collection, with_payload=["chunk_id", "content_hash"],
                with_vectors=False, limit=1000, offset=offset)
            for r in recs:
                p = r.payload or {}
                cid = p.get("chunk_id")
                if cid:
                    out[cid] = str(p.get("content_hash", ""))
            if offset is None:
                break
        return out

    def delete_by_chunk_ids(self, chunk_ids: list[str]) -> int:
        from qdrant_client import models
        if not chunk_ids:
            return 0
        ids = [point_uuid(c) for c in chunk_ids]
        self.client.delete(self.s.collection,
                           points_selector=models.PointIdsList(points=ids))
        return len(chunk_ids)

    @staticmethod
    def _to_chunk(point) -> RetrievedChunk:
        payload = dict(point.payload or {})
        text = payload.pop("text", "")
        return RetrievedChunk(chunk_id=payload.get("chunk_id", ""), text=text,
                              score=float(getattr(point, "score", 0.0) or 0.0),
                              payload=payload)
