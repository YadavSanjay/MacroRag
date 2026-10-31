"""Corpus browse surface (UI design doc area A1 — Corpus Explorer).

Read-only, facet-driven views over the persisted ingest output (the processed
``ParsedDocument`` JSON under ``settings.processed_path``) plus the numeric
series catalog. Reads the JSON contract directly — no models, no vector store —
so it works before the index is built and is cheap to test.

Routes are gated by the normal request auth (principal), like the rest of the
business API; the corpus is the shared knowledge base, so no per-caller filtering is
applied at the file level.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query

from utils.config import RagSettings
from utils.corpus import iter_documents
from utils.logging import get_logger
from utils.objectstore import ObjectStore, corpus_store

log = get_logger(__name__)

# Cache parsed docs per store location, invalidated when the object set / mtimes
# change (``ObjectStore.signature``).
_cache: dict[str, tuple[tuple[int, float], list[dict[str, Any]]]] = {}


def load_corpus(store: ObjectStore) -> list[dict[str, Any]]:
    key = store.location
    sig = store.signature()
    hit = _cache.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    docs = list(iter_documents(store))
    _cache[key] = (sig, docs)
    return docs


def _doc_tags(doc: dict) -> tuple[list[str], list[str]]:
    """Union of entity/factor tags across a document's chunks."""
    entity: list[str] = []
    factor: list[str] = []
    for ch in doc.get("chunks") or []:
        meta = ch.get("metadata", {})
        for t in meta.get("entity_tags") or []:
            if t not in entity:
                entity.append(t)
        for t in meta.get("factor_tags") or []:
            if t not in factor:
                factor.append(t)
    return entity, factor


def doc_summary(doc: dict) -> dict[str, Any]:
    entity, factor = _doc_tags(doc)
    return {
        "doc_id": doc.get("doc_id"),
        "source_id": doc.get("source_id", ""),
        "title": doc.get("title") or doc.get("publication") or doc.get("doc_id"),
        "publisher": doc.get("publisher", ""),
        "publication": doc.get("publication", ""),
        "release_date": doc.get("release_date"),
        "doc_type": doc.get("doc_type", "unknown"),
        "tier": doc.get("tier"),
        "bucket": doc.get("bucket"),
        "n_chunks": len(doc.get("chunks") or []),
        "n_timeseries": len(doc.get("timeseries") or []),
        "entity_tags": entity,
        "factor_tags": factor,
    }


def _counts(values: list[Any]) -> list[dict[str, Any]]:
    out: dict[Any, int] = {}
    for v in values:
        if v in (None, ""):
            continue
        out[v] = out.get(v, 0) + 1
    return [{"value": k, "count": c}
            for k, c in sorted(out.items(), key=lambda kv: (-kv[1], str(kv[0])))]


def build_facets(summaries: list[dict]) -> dict[str, Any]:
    dates = [s["release_date"] for s in summaries if s["release_date"]]
    return {
        "n_documents": len(summaries),
        "n_chunks": sum(s["n_chunks"] for s in summaries),
        "n_timeseries_docs": sum(1 for s in summaries if s["n_timeseries"] > 0),
        "date_range": {"min": min(dates) if dates else None,
                       "max": max(dates) if dates else None},
        "tiers": _counts([s["tier"] for s in summaries]),
        "buckets": _counts([s["bucket"] for s in summaries]),
        "doc_types": _counts([s["doc_type"] for s in summaries]),
        "publishers": _counts([s["publisher"] for s in summaries]),
        "entity_tags": _counts([t for s in summaries for t in s["entity_tags"]]),
        "factor_tags": _counts([t for s in summaries for t in s["factor_tags"]]),
    }


def _match(s: dict, *, tiers, doc_types, publishers, buckets,
           entity_tags, factor_tags, date_from, date_to, q) -> bool:
    if tiers and s["tier"] not in tiers:
        return False
    if doc_types and s["doc_type"] not in doc_types:
        return False
    if publishers and s["publisher"] not in publishers:
        return False
    if buckets and s["bucket"] not in buckets:
        return False
    if entity_tags and not (set(entity_tags) & set(s["entity_tags"])):
        return False
    if factor_tags and not (set(factor_tags) & set(s["factor_tags"])):
        return False
    rd = s["release_date"]
    if date_from and (not rd or rd < date_from):
        return False
    if date_to and (not rd or rd > date_to):
        return False
    if q:
        hay = f"{s['title']} {s['publication']} {s['source_id']}".lower()
        if q.lower() not in hay:
            return False
    return True


def _sort_key(s: dict) -> tuple:
    # newest first; documents without a release_date sort last
    rd = s["release_date"]
    return (0, rd) if rd else (1, "")


def document_detail(doc: dict) -> dict[str, Any]:
    summary = doc_summary(doc)
    chunks = [
        {
            "chunk_id": ch.get("chunk_id"),
            "seq": ch.get("seq", 0),
            "section": ch.get("metadata", {}).get("section"),
            "n_chars": len(ch.get("text", "")),
            "entity_tags": ch.get("metadata", {}).get("entity_tags") or [],
            "factor_tags": ch.get("metadata", {}).get("factor_tags") or [],
            "text": ch.get("text", ""),
        }
        for ch in doc.get("chunks") or []
    ]
    ts = doc.get("timeseries") or []
    ts_sources = sorted({r.get("source") for r in ts if r.get("source")})
    ts_vars = sorted({r.get("variable") for r in ts if r.get("variable")})
    return {
        **summary,
        "url": doc.get("url"),
        "chunks": chunks,
        "timeseries": {
            "n_rows": len(ts),
            "sources": ts_sources,
            "variables": ts_vars,
            "sample": ts[:10],
        },
    }


def series_catalog(docs: list[dict]) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    total = 0
    for doc in docs:
        for row in doc.get("timeseries") or []:
            total += 1
            src = row.get("source") or row.get("source_id") or doc.get("source_id") or "—"
            entry = by_source.setdefault(
                src, {"source": src, "n_rows": 0, "variables": set(), "publishers": set()})
            entry["n_rows"] += 1
            if row.get("variable"):
                entry["variables"].add(row["variable"])
            pub = doc.get("publisher")
            if pub:
                entry["publishers"].add(pub)
    sources = [
        {"source": e["source"], "n_rows": e["n_rows"],
         "variables": sorted(e["variables"]), "publishers": sorted(e["publishers"])}
        for e in by_source.values()
    ]
    sources.sort(key=lambda e: (-e["n_rows"], e["source"]))
    return {"n_rows_total": total, "sources": sources}


def add_corpus_routes(
    app: FastAPI,
    *,
    settings: RagSettings,
    auth_dep: Callable[..., Any],
) -> None:
    """Register the ``/corpus/*`` read endpoints on ``app``."""

    def _summaries() -> list[dict]:
        return [doc_summary(d) for d in load_corpus(corpus_store(settings))]

    @app.get("/corpus/facets")
    def corpus_facets(_: Any = Depends(auth_dep)):
        return build_facets(_summaries())

    @app.get("/corpus/documents")
    def corpus_documents(
        tier: list[int] | None = Query(None),
        doc_type: list[str] | None = Query(None),
        publisher: list[str] | None = Query(None),
        bucket: list[str] | None = Query(None),
        entity_tag: list[str] | None = Query(None),
        factor_tag: list[str] | None = Query(None),
        date_from: str | None = Query(None),
        date_to: str | None = Query(None),
        q: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        _: Any = Depends(auth_dep),
    ):
        matched = [
            s for s in _summaries()
            if _match(s, tiers=tier, doc_types=doc_type, publishers=publisher,
                      buckets=bucket, entity_tags=entity_tag, factor_tags=factor_tag,
                      date_from=date_from, date_to=date_to, q=q)
        ]
        matched.sort(key=_sort_key, reverse=True)
        return {
            "total": len(matched),
            "limit": limit,
            "offset": offset,
            "documents": matched[offset: offset + limit],
        }

    @app.get("/corpus/documents/{doc_id}")
    def corpus_document(doc_id: str, _: Any = Depends(auth_dep)):
        for doc in load_corpus(corpus_store(settings)):
            if doc.get("doc_id") == doc_id:
                return document_detail(doc)
        raise HTTPException(status_code=404, detail="document not found")

    @app.get("/corpus/series")
    def corpus_series(_: Any = Depends(auth_dep)):
        return series_catalog(load_corpus(corpus_store(settings)))
