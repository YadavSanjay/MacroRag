"""Admin / system surface (UI design doc §7, area B).

Read-only introspection endpoints that back the technical "System" area of the
web UI — index manifest + staleness, active configuration, query traces, and
vector/time-series store stats. Mutating job endpoints (ingest/index/eval) are a
later phase; this module is deliberately read-only.

All routes sit under ``/admin`` and require an admin-authorised API key when
``auth.enabled`` (see :func:`api.app.create_app`). They never expose secrets:
the config view returns model names and thresholds, never API keys.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from utils.config import RagSettings
from utils.logging import get_logger
from utils.manifest import PIPELINE_VERSION, is_stale, prompt_hash, read_manifest, world_view_id
from utils.objectstore import corpus_store, manifest_store

from .jobs import Job, JobConflict, JobManager
from .models import FilterModel, chunk_brief

log = get_logger(__name__)


class IndexRequest(BaseModel):
    append: bool = False     # append to the existing index instead of recreating
    # Diff-based refresh (embed only new + changed chunks, delete departed) and its
    # plan-only mode (spec admin-index-incremental); the CLI's `index --incremental`.
    incremental: bool = False
    dry_run: bool = False

    @model_validator(mode="after")
    def _check_mode(self) -> IndexRequest:
        """A dry run is a plan of the refresh, and a refresh never recreates or appends
        wholesale, so both invalid combinations are refused (422)."""
        if self.dry_run and not self.incremental:
            raise ValueError("dry_run previews the incremental refresh: set incremental too")
        if self.incremental and self.append:
            raise ValueError("incremental and append are different index modes: pick one")
        return self


class VectorSearchRequest(BaseModel):
    """Raw vector-store probe (pre-rerank): embed a query and return the fused
    dense+sparse candidates, so an operator can inspect what retrieval sees before
    the cross-encoder rerank + gating."""
    query: str
    limit: int = 10
    filter: FilterModel | None = None


class EvalRequest(BaseModel):
    k: int = 8               # rank cutoff for hit@k / recall@k
    judge: bool = False      # §B: also run the LLM-judge answer-quality pass


class SelfCheckRequest(BaseModel):
    """Self-validation run: questions are generated from the time-series store at
    run time, so there is no golden file to pass — only sampling breadth."""
    per_group: int = 2          # observations sampled per (source, variable, scenario)
    max_cases: int = 100        # hard cap on generated numeric cases
    per_kind: int = 4           # negative controls per probe kind
    sources: list[str] | None = None   # restrict to named store sources


class BacktestRequest(BaseModel):
    """Realized-episode backtest. Episodes and their references come from the
    ingested historic series, so there is nothing to author — only a selection."""
    episodes: list[str] | None = None   # episode keys; None = the whole catalog
    modes: list[str] | None = None      # grounding_fidelity | ex_ante_skill


class BaselineRequest(BaseModel):
    summary: dict[str, float]   # eval summary to promote to the drift baseline


class VocabularyUpdate(BaseModel):
    """§E vocabulary edit — only provided fields are changed (None = leave as-is)."""
    factor_tags: list[str] | None = Field(default=None, max_length=2000)
    entity_tags: list[str] | None = Field(default=None, max_length=2000)
    aliases: dict[str, str] | None = None


class IngestRequest(BaseModel):
    tier: str
    endpoints: list[str] | None = None   # subset of endpoint ids (default: all enabled)
    limit: int | None = None             # max new docs per endpoint (smoke testing)
    refresh: bool = False                   # bypass discovery cache


# Curation tasks wrap the maintenance scripts under scripts/.
CURATION_TASKS = ("normalize-vocab", "rechunk", "reextract-sep", "reextract-dfast")


class CurateRequest(BaseModel):
    task: str
    dry_run: bool = True       # safe default — report only (where the task supports it)
    strip_only: bool = False   # reextract-dfast: skip CSV ingest, only retire legacy grid


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file (oldest first). Missing file -> empty; skips bad lines."""
    if not Path(path).exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:   # skip a corrupt line, keep the rest
                    continue
    except OSError as exc:  # pragma: no cover
        log.warning("jsonl read failed (%s): %s", path, exc)
    return out


def _corpus_freshness(source) -> dict[int, dict]:
    """Scan the processed-corpus doc headers (JSON, cheap — no chunk bodies needed)
    and return per-tier freshness: the latest ``retrieval_date`` / ``release_date``
    seen and a doc count (§E). Keyed by tier number. ``source`` is an ObjectStore
    (local FS or S3) or a directory path. Resilient to a missing/empty corpus."""
    from datetime import datetime, timezone

    from utils.corpus import iter_documents
    out: dict[int, dict] = {}
    for doc in iter_documents(source):
        tier = doc.get("tier")
        if not isinstance(tier, int):
            continue
        agg = out.setdefault(tier, {"last_retrieval_date": None,
                                    "last_release_date": None, "n_docs": 0})
        agg["n_docs"] += 1
        for key, field in (("last_retrieval_date", "retrieval_date"),
                           ("last_release_date", "release_date")):
            val = doc.get(field)
            if val and (agg[key] is None or str(val) > agg[key]):
                agg[key] = str(val)
    # days since last ingest (staleness signal; the UI decides "overdue" per cadence)
    today = datetime.now(UTC).date()
    for agg in out.values():
        lr = agg["last_retrieval_date"]
        try:
            agg["days_since_ingest"] = (today - datetime.fromisoformat(
                str(lr)[:10]).date()).days if lr else None
        except ValueError:
            agg["days_since_ingest"] = None
    return out


_CALL_BODY_FIELDS = ("system", "user", "response")


def _slim_trace(rec: dict[str, Any]) -> dict[str, Any]:
    """A trace record for the LIST view: identical to the stored record but with
    each LLM call's prompt/response bodies stripped (kept per-call token/cost
    metadata). Full bodies are served by the single-trace detail endpoint."""
    usage = rec.get("usage")
    if not isinstance(usage, dict) or not usage.get("call_log"):
        return rec
    slim_calls = [
        {k: v for k, v in call.items() if k not in _CALL_BODY_FIELDS}
        for call in usage["call_log"]
    ]
    return {**rec, "usage": {**usage, "call_log": slim_calls}}


def _paged_recent(records: list[dict], offset: int, limit: int) -> dict[str, Any]:
    """Most-recent-first window with a running index, for trace/feedback views."""
    recent = list(reversed(records))
    page = recent[offset: offset + limit]
    return {
        "total": len(records),
        "limit": limit,
        "offset": offset,
        "items": [{"i": offset + idx, **rec} for idx, rec in enumerate(page)],
    }


def _config_view(s: RagSettings) -> dict[str, Any]:
    """Sanitised settings — model names + thresholds, never secrets/keys."""
    return {
        "embedding": {
            "model": s.embedding.model,
            "dense_dim": s.embedding.dense_dim,
            "grounding_prefix": s.embedding.use_grounding_prefix,
        },
        "vector_store": {
            "collection": s.qdrant.collection,
            "mode": ("server" if s.qdrant.url else
                     "memory" if s.qdrant.location == ":memory:" else "local"),
            "quantization": s.qdrant.quantization,
        },
        "reranker": {"enabled": s.reranker.enabled, "model": s.reranker.model},
        "llm": {
            "provider": s.llm.provider,
            "plan_model": s.llm.plan_model,
            "answer_model": s.llm.answer_model,
            "complex_model": s.llm.complex_model,
            "complex_routes": s.llm.complex_routes,
        },
        "retrieval": {
            "final_k": s.retrieval.final_k,
            "rerank_candidates": s.retrieval.rerank_candidates,
            "mmr_lambda": s.retrieval.mmr_lambda,
            "min_rerank_score": s.retrieval.min_rerank_score,
            "chunk_expansion": s.retrieval.chunk_expansion,
            "hydrate_timeseries": s.retrieval.hydrate_timeseries,
            "plan_execute": s.retrieval.plan_execute,
            "verify_faithfulness": s.retrieval.verify_faithfulness,
        },
        "ranking": {
            "time_decay": s.ranking.time_decay,
            "half_life_days": s.ranking.half_life_days,
        },
        "cache": {"enabled": s.cache.enabled, "backend": s.cache.backend,
                  "semantic": s.semantic_cache.enabled},
        "auth": {"enabled": s.auth.enabled},   # never the keys themselves
        "observability": {"otel_enabled": s.observability.otel_enabled,
                          "service_name": s.observability.service_name},
    }


def _active_impls(pipe: object) -> dict[str, str | None]:
    """The concrete interface implementations behind the built pipeline."""
    retriever = getattr(pipe, "retriever", None)
    ts = getattr(pipe, "timeseries", None)
    return {
        "embedder": _type_name(getattr(retriever, "embedder", None)),
        "vector_store": _type_name(getattr(retriever, "store", None)),
        "reranker": _type_name(getattr(retriever, "reranker", None)),
        "analyzer": _type_name(getattr(retriever, "analyzer", None)),
        "llm": _type_name(getattr(pipe, "llm", None)),
        "timeseries_store": _type_name(ts),
        "agent": _type_name(getattr(pipe, "agent", None)),
    }


def _type_name(obj: object) -> str | None:
    return type(obj).__name__ if obj is not None else None


# ── dependency health probes (Tier 2) ─────────────────────────────────────────


def _probe(name: str, fn: Callable[[], tuple[str, str]]) -> dict[str, Any]:
    """Run one dependency check, timing it and turning any exception into a
    ``down`` result so a single unreachable dependency can't 500 the endpoint."""
    start = time.time()
    try:
        status, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a probe failure IS the signal
        status, detail = "down", str(exc)[:200]
    return {"name": name, "status": status, "detail": detail,
            "latency_ms": round((time.time() - start) * 1000, 1)}


def _probe_health(settings: RagSettings, peek: Callable[[], Any | None]) -> list[dict]:
    """Probe the live dependencies: vector store, time-series store, the shared
    inference service, and the LLM provider credential. Deliberately avoids
    forcing a heavy model load — it uses the already-built pipeline when present,
    otherwise a cheap connectivity/credential check."""

    def _vector() -> tuple[str, str]:
        built = peek()
        if built is not None:
            store = built.retriever.store
            return "ok", f"{store.count()} points ({_type_name(store)})"
        if settings.qdrant.url:
            from qdrant_client import QdrantClient
            cli = QdrantClient(url=settings.qdrant.url, timeout=3)
            exists = cli.collection_exists(settings.qdrant.collection)
            return ("ok" if exists else "down"), (
                f"server {settings.qdrant.url}, collection "
                f"'{settings.qdrant.collection}' {'present' if exists else 'MISSING'}")
        return "skipped", "on-disk/memory store — loaded lazily on first query"

    def _timeseries() -> tuple[str, str]:
        from vectorstores.timeseries_store import TimeseriesStore
        path = settings.timeseries_path
        if not Path(path).exists():
            return "down", f"no store at {path}"
        return "ok", f"{TimeseriesStore(path).count()} observations"

    def _inference() -> tuple[str, str]:
        import os
        import urllib.error
        import urllib.request
        url = os.environ.get("RAGINFER_URL", settings.inference.remote_url)
        if not url:
            return "skipped", "in-process models (no remote inference service)"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:   # noqa: S310
                return "ok", f"{url} (HTTP {resp.status})"
        except urllib.error.HTTPError as exc:  # server responded => reachable
            return "ok", f"{url} (HTTP {exc.code})"
        except Exception as exc:  # noqa: BLE001 - connection refused / timeout
            return "down", f"unreachable at {url}: {str(exc)[:120]}"

    def _llm() -> tuple[str, str]:
        import os
        env = settings.llm.api_key_env
        present = bool(os.environ.get(env))
        return ("ok" if present else "down"), (
            f"{settings.llm.provider} · {settings.llm.answer_model} · "
            f"{env} {'present' if present else 'MISSING'}")

    return [
        _probe("vector_store", _vector),
        _probe("timeseries_store", _timeseries),
        _probe("inference_service", _inference),
        _probe("llm_provider", _llm),
    ]


def _overall_health(checks: list[dict]) -> str:
    """down if the vector store (the retrieval-critical dep) is down; degraded if
    any other dep is down; else ok. 'skipped' never degrades."""
    by = {c["name"]: c["status"] for c in checks}
    if by.get("vector_store") == "down":
        return "down"
    if any(c["status"] == "down" for c in checks):
        return "degraded"
    return "ok"


# ── trace metrics roll-up (Tier 2) ────────────────────────────────────────────


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def _trace_latency_ms(rec: dict) -> float:
    timings = rec.get("timings_ms") or {}
    return float(sum(v for v in timings.values() if isinstance(v, (int, float))))


def _aggregate_metrics(records: list[dict]) -> dict[str, Any]:
    """Roll up query traces into cost/latency/route/model/day aggregates for the
    operator dashboard. Pure function over the trace JSONL — no models touched."""
    n = len(records)
    refused = sum(1 for r in records if r.get("refused"))
    cached = sum(1 for r in records if r.get("cached"))
    total_cost = 0.0
    cost_complete = True
    tok = {"prompt": 0, "completion": 0, "cached": 0, "total": 0}
    latencies: list[float] = []
    by_route: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    daily: dict[str, dict] = {}

    for r in records:
        usage = r.get("usage") or {}
        cost = usage.get("cost_usd") or 0.0
        total_cost += cost
        if usage and not usage.get("cost_complete", True):
            cost_complete = False
        tok["prompt"] += usage.get("prompt_tokens", 0) or 0
        tok["completion"] += usage.get("completion_tokens", 0) or 0
        tok["cached"] += usage.get("cached_tokens", 0) or 0
        tok["total"] += usage.get("total_tokens", 0) or 0

        lat = _trace_latency_ms(r)
        latencies.append(lat)

        route = r.get("route") or "UNKNOWN"
        rr = by_route.setdefault(route, {"route": route, "n": 0, "cost_usd": 0.0,
                                         "latency_sum": 0.0, "refused": 0})
        rr["n"] += 1
        rr["cost_usd"] += cost
        rr["latency_sum"] += lat
        if r.get("refused"):
            rr["refused"] += 1

        for call in usage.get("call_log", []) or []:
            model = call.get("model") or "unknown"
            mm = by_model.setdefault(model, {"model": model, "calls": 0,
                                             "total_tokens": 0, "cost_usd": 0.0})
            mm["calls"] += 1
            mm["total_tokens"] += call.get("total_tokens", 0) or 0
            mm["cost_usd"] += call.get("cost_usd") or 0.0

        ts = r.get("ts")
        if isinstance(ts, str) and len(ts) >= 10:
            day = ts[:10]
            dd = daily.setdefault(day, {"date": day, "n": 0, "cost_usd": 0.0,
                                        "latency_sum": 0.0})
            dd["n"] += 1
            dd["cost_usd"] += cost
            dd["latency_sum"] += lat

    routes = sorted(
        ({"route": r["route"], "n": r["n"], "cost_usd": round(r["cost_usd"], 6),
          "avg_latency_ms": round(r["latency_sum"] / r["n"], 1) if r["n"] else 0.0,
          "refused": r["refused"]} for r in by_route.values()),
        key=lambda x: -x["n"])
    models = sorted(
        ({"model": m["model"], "calls": m["calls"], "total_tokens": m["total_tokens"],
          "cost_usd": round(m["cost_usd"], 6)} for m in by_model.values()),
        key=lambda x: -x["cost_usd"])
    days = [{"date": d["date"], "n": d["n"], "cost_usd": round(d["cost_usd"], 6),
             "avg_latency_ms": round(d["latency_sum"] / d["n"], 1) if d["n"] else 0.0}
            for d in sorted(daily.values(), key=lambda x: x["date"])]

    return {
        "n_traces": n,
        "queries": {
            "total": n, "refused": refused,
            "refusal_rate": round(refused / n, 4) if n else 0.0,
            "cached": cached,
            "cache_hit_rate": round(cached / n, 4) if n else 0.0,
        },
        "cost": {
            "total_usd": round(total_cost, 6), "complete": cost_complete,
            "prompt_tokens": tok["prompt"], "completion_tokens": tok["completion"],
            "cached_tokens": tok["cached"], "total_tokens": tok["total"],
        },
        "latency_ms": {
            "avg": round(sum(latencies) / n, 1) if n else 0.0,
            "p50": round(_percentile(latencies, 50), 1),
            "p95": round(_percentile(latencies, 95), 1),
        },
        "by_route": routes,
        "by_model": models,
        "daily": days,
    }


def add_admin_routes(
    app: FastAPI,
    *,
    settings: RagSettings,
    pipe: Callable[[], Any],
    peek: Callable[[], Any | None],
    admin_dep: Callable[..., Any],
    jobs: JobManager,
    index_runner: Callable[[Job], dict | None],
    eval_runner: Callable[[Job], dict | None],
    selfcheck_runner: Callable[[Job], dict | None],
    backtest_runner: Callable[[Job], dict | None],
    ingest_runner: Callable[[Job], dict | None],
    curation_runner: Callable[[Job], dict | None],
) -> None:
    """Register the ``/admin/*`` read + job endpoints on ``app``.

    ``pipe`` builds the pipeline (lazy, heavy); ``peek`` returns it only if it is
    already built (so config/manifest don't trigger a model load); ``admin_dep``
    is the auth dependency; ``jobs`` runs background maintenance tasks;
    ``index_runner``/``eval_runner`` are the callables those jobs execute.
    """

    @app.get("/admin/manifest")
    def admin_manifest(_: Any = Depends(admin_dep)):
        return {
            "manifest": read_manifest(manifest_store(settings)),
            "stale": is_stale(settings),
            "world_view_id": world_view_id(settings),
            "pipeline_version": PIPELINE_VERSION,
            "prompt_hash": prompt_hash(),
            "built": peek() is not None,
        }

    @app.get("/admin/manifest/history")
    def admin_manifest_history(
        limit: int = Query(50, ge=1, le=250),
        _: Any = Depends(admin_dep),
    ):
        """Recorded index builds, most recent first, each with the field-level
        diff against the build before it — "what moved between W1 and W2"."""
        from utils.manifest import diff_manifests, read_manifest_history
        builds = read_manifest_history(manifest_store(settings), limit=limit)
        for newer, older in zip(builds, builds[1:]):
            newer["changes"] = diff_manifests(older, newer)
        if builds:
            builds[-1].setdefault("changes", [])   # oldest in the window
        return {"total": len(builds), "builds": builds,
                "current": world_view_id(settings)}

    @app.get("/admin/manifest/verify")
    def admin_manifest_verify(
        world_view: str = Query(..., min_length=1, max_length=200),
        _: Any = Depends(admin_dep),
    ):
        """Audit lookup: was this ``world_view_id`` ever our corpus, and is it
        still current? Answers the question asked of an old shock vector.

        ``known=false`` is not proof of forgery — history only covers builds
        recorded since this log existed."""
        from utils.manifest import find_build
        build = find_build(manifest_store(settings), world_view)
        current = world_view_id(settings)
        return {"world_view_id": world_view, "known": build is not None,
                "current": current, "is_current": bool(current)
                and world_view == current, "build": build}

    @app.get("/admin/config")
    def admin_config(_: Any = Depends(admin_dep)):
        view = _config_view(settings)
        built = peek()
        view["built"] = built is not None
        view["active_impls"] = _active_impls(built) if built is not None else None
        return view

    @app.get("/admin/traces")
    def admin_traces(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        _: Any = Depends(admin_dep),
    ):
        records = _read_jsonl(settings.trace_path)
        recent = list(reversed(records))   # most recent first
        page = recent[offset: offset + limit]
        return {
            "total": len(records),
            "limit": limit,
            "offset": offset,
            # List stays lean: the (potentially large) per-call prompt/response
            # bodies are dropped here and served only by /admin/traces/{idx}.
            "traces": [{"i": offset + idx, **_slim_trace(rec)}
                       for idx, rec in enumerate(page)],
        }

    @app.get("/admin/traces/{idx}")
    def admin_trace(idx: int, _: Any = Depends(admin_dep)):
        recent = list(reversed(_read_jsonl(settings.trace_path)))
        if idx < 0 or idx >= len(recent):
            raise HTTPException(status_code=404, detail="trace not found")
        return {"i": idx, **recent[idx]}   # full record incl. prompts/responses

    @app.get("/admin/health")
    def admin_health(_: Any = Depends(admin_dep)):
        checks = _probe_health(settings, peek)
        return {"status": _overall_health(checks),
                "built": peek() is not None, "checks": checks}

    @app.get("/admin/metrics")
    def admin_metrics(
        limit: int = Query(2000, ge=1, le=100000),
        _: Any = Depends(admin_dep),
    ):
        records = _read_jsonl(settings.trace_path)
        if len(records) > limit:            # aggregate the most recent `limit` traces
            records = records[-limit:]
        return _aggregate_metrics(records)

    @app.get("/admin/schema-health")
    def admin_schema_health(_: Any = Depends(admin_dep)):
        """Structured-output health per task/model: ok/repaired/failed counts +
        repair_rate since process start (§B / Phase 2). A rising repair_rate for a
        task/model flags model drift on structured output."""
        from llm.schema_health import schema_health_snapshot
        return schema_health_snapshot()

    @app.get("/admin/stores/vector")
    def admin_store_vector(_: Any = Depends(admin_dep)):
        store = pipe().retriever.store
        return {
            "backend": _type_name(store),
            "count": store.count(),
            "collection": settings.qdrant.collection,
        }

    @app.post("/admin/stores/vector/search")
    def admin_store_vector_search(req: VectorSearchRequest, _: Any = Depends(admin_dep)):
        p = pipe()                          # builds the pipeline (embedder + store)
        emb = p.retriever.embedder.embed_query(req.query)
        mf = req.filter.to_meta() if req.filter else None
        limit = max(1, min(50, req.limit))
        hits = p.retriever.store.search(emb, limit=limit, filt=mf)
        return {
            "query": req.query,
            "n": len(hits),
            # fused dense+sparse candidates, pre-rerank (score = RRF/dense score)
            "candidates": [
                {**chunk_brief(h), "score": h.score, "snippet": (h.text or "")[:300]}
                for h in hits
            ],
        }

    @app.get("/admin/stores/timeseries")
    def admin_store_timeseries(_: Any = Depends(admin_dep)):
        from vectorstores.timeseries_store import TimeseriesStore

        path = settings.timeseries_path
        if not Path(path).exists():
            return {"available": False, "path": str(path), "count": 0, "sources": []}
        ts = TimeseriesStore(path)
        return {
            "available": True,
            "path": str(path),
            "count": ts.count(),
            "sources": ts.sources(),
        }

    @app.get("/admin/stores/timeseries/query")
    def admin_store_timeseries_query(
        source: str | None = None,
        variable: str | None = None,
        scenario: str | None = None,
        limit: int = Query(200, ge=1, le=2000),
        _: Any = Depends(admin_dep),
    ):
        from vectorstores.timeseries_store import TimeseriesStore

        path = settings.timeseries_path
        if not Path(path).exists():
            return {"available": False, "n": 0, "rows": []}
        rows = TimeseriesStore(path).query(
            source=source or None, variable=variable or None,
            scenario=scenario or None, limit=limit)
        # drop the heavy per-row `raw` JSON blob for the table view
        slim = [{k: v for k, v in r.items() if k != "raw"} for r in rows]
        return {"available": True, "n": len(slim), "rows": slim}

    # ── jobs (maintenance actions) ──────────────────────────────────────────

    def _submit_exclusive(type_: str, params: dict, runner: Callable[[Job], dict | None]):
        """Submit a store-mutating job in the shared ``corpus`` exclusive group,
        mapping a rejected concurrent build to HTTP 409 (§H — prevents the
        concurrent-Qdrant-writer deadlock)."""
        try:
            job = jobs.submit(type_, params, runner, exclusive_group="corpus")
        except JobConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return job.to_dict(with_logs=False)

    @app.post("/admin/index")
    def admin_index(req: IndexRequest, _: Any = Depends(admin_dep)):
        return _submit_exclusive("index", {"append": req.append,
                                           "incremental": req.incremental,
                                           "dry_run": req.dry_run}, index_runner)

    @app.get("/admin/jobs")
    def admin_jobs(_: Any = Depends(admin_dep)):
        return {"jobs": jobs.list()}

    @app.get("/admin/jobs/{job_id}")
    def admin_job(job_id: str, _: Any = Depends(admin_dep)):
        # get_dict falls back to the persisted record, so a job owned by another
        # replica (or one from before a restart) is still visible (§I).
        rec = jobs.get_dict(job_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="job not found")
        return rec

    @app.post("/admin/jobs/{job_id}/cancel")
    def admin_job_cancel(job_id: str, _: Any = Depends(admin_dep)):
        job = jobs.cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job.to_dict(with_logs=False)

    @app.get("/admin/jobs/{job_id}/stream")
    def admin_job_stream(job_id: str, _: Any = Depends(admin_dep)):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        def events():
            sent = 0
            deadline = time.time() + 1800        # safety cap (30 min)
            while True:
                for entry in job.logs_since(sent):
                    yield _sse("log", entry)
                    sent += 1
                if job.is_terminal:
                    yield _sse("status", {"status": job.status,
                                          "result": job.result, "error": job.error})
                    yield _sse("done", {})
                    return
                if time.time() > deadline:       # pragma: no cover - safety only
                    yield _sse("done", {})
                    return
                time.sleep(0.2)

        return StreamingResponse(events(), media_type="text/event-stream")

    # ── quality: golden set, eval, drift baseline, feedback ─────────────────

    @app.get("/admin/golden")
    def admin_golden(_: Any = Depends(admin_dep)):
        from eval.harness import load_golden
        try:
            questions = load_golden(settings.golden_path)
        except OSError:
            questions = []
        return {"path": str(settings.golden_path), "questions": questions}

    @app.post("/admin/eval")
    def admin_eval(req: EvalRequest, _: Any = Depends(admin_dep)):
        job = jobs.submit("eval", {"k": req.k, "judge": req.judge}, eval_runner)
        return job.to_dict(with_logs=False)

    @app.post("/admin/selfcheck")
    def admin_selfcheck(req: SelfCheckRequest, _: Any = Depends(admin_dep)):
        """Differential self-validation against the stored numeric answer key —
        no golden file, no LLM judge (eval/selfcheck.py)."""
        job = jobs.submit("selfcheck", req.model_dump(), selfcheck_runner)
        return job.to_dict(with_logs=False)

    @app.get("/admin/backtest/episodes")
    def admin_backtest_episodes(_: Any = Depends(admin_dep)):
        """The episode catalog with each episode's realized reference, derived
        live from the ingested historic series (so it reflects the current
        corpus, not a checked-in expectation)."""
        from eval.episodes import EPISODES, derive_realized_reference, point_in_time_cutoff
        from vectorstores.timeseries_store import TimeseriesStore
        path = settings.timeseries_path
        store = TimeseriesStore(path) if Path(path).exists() else None
        out = []
        for ep in EPISODES:
            reference = derive_realized_reference(store, ep) if store else {}
            out.append({"key": ep.key, "aka": ep.aka, "scenario": ep.scenario,
                        "start": ep.start, "end": ep.end,
                        "pit_cutoff": point_in_time_cutoff(ep),
                        "reference": reference, "fields": sorted(reference)})
        return {"total": len(out), "episodes": out}

    @app.post("/admin/backtest")
    def admin_backtest(req: BacktestRequest, _: Any = Depends(admin_dep)):
        """Score shock vectors against realized episode moves (eval/episodes.py)."""
        job = jobs.submit("backtest", req.model_dump(), backtest_runner)
        return job.to_dict(with_logs=False)

    @app.post("/admin/feedback/reconcile")
    def admin_feedback_reconcile(_: Any = Depends(admin_dep)):
        """§B: turn positive, chunk-citing feedback into *candidate* golden entries
        (deduped vs the live golden set) and write them to the review file. Does NOT
        promote into the golden set — that's a human decision."""
        from eval.feedback_reconcile import reconcile_feedback, write_candidates
        candidates = reconcile_feedback(settings.feedback_path, settings.golden_path)
        path = write_candidates(candidates, settings.golden_candidates_path)
        return {"n_proposed": len(candidates), "candidates": candidates,
                "written_to": str(path)}

    @app.get("/admin/eval/history")
    def admin_eval_history(
        limit: int = Query(50, ge=1, le=1000),
        _: Any = Depends(admin_dep),
    ):
        records = _read_jsonl(settings.eval_history_path)
        recent = list(reversed(records))[:limit]   # most recent first
        return {"total": len(records), "runs": recent}

    @app.get("/admin/eval/baseline")
    def admin_eval_baseline_get(_: Any = Depends(admin_dep)):
        from eval.drift import load_baseline
        return {"baseline": load_baseline(settings.eval_baseline_path)}

    @app.post("/admin/eval/baseline")
    def admin_eval_baseline_set(req: BaselineRequest, _: Any = Depends(admin_dep)):
        from eval.drift import save_baseline
        save_baseline(settings.eval_baseline_path, req.summary)
        return {"status": "saved", "baseline": req.summary}

    @app.get("/admin/feedback")
    def admin_feedback(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        _: Any = Depends(admin_dep),
    ):
        # Records written before the rename carry ``tenant``; normalise so the UI
        # sees one field regardless of when the row was written.
        records = []
        for r in _read_jsonl(settings.feedback_path):
            if "principal" not in r and "tenant" in r:
                r = {**r, "principal": r.get("tenant")}
            records.append(r)
        return _paged_recent(records, offset, limit)

    # ── ingestion (tier catalog + ingest job) ───────────────────────────────

    @app.get("/admin/tiers")
    def admin_tiers(_: Any = Depends(admin_dep)):
        from ingest.common.config import load_tier_config
        from utils.config import REPO_ROOT
        # §E: join the static catalog with corpus freshness (last-ingested per tier).
        freshness = _corpus_freshness(corpus_store(settings))
        out = []
        for f in sorted((REPO_ROOT / "configs" / "ingest").glob("tier*.yaml")):
            try:
                t = load_tier_config(f.stem)
                out.append({
                    "name": f.stem, "tier": t.tier, "title": t.name,
                    "publisher": t.publisher,
                    "n_endpoints": len(t.endpoints),
                    "n_enabled": sum(1 for e in t.endpoints if e.enabled),
                    "freshness": freshness.get(t.tier),   # None if nothing ingested yet
                })
            except Exception as exc:   # a broken tier file shouldn't hide the rest
                out.append({"name": f.stem, "error": str(exc)})
        return {"tiers": out}

    @app.get("/admin/tiers/{name}")
    def admin_tier(name: str, _: Any = Depends(admin_dep)):
        from ingest.common.config import load_tier_config
        try:
            t = load_tier_config(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="tier not found")
        return {
            "name": name, "tier": t.tier, "title": t.name,
            "publisher": t.publisher, "licence": t.licence, "base_url": t.base_url,
            "endpoints": [
                {
                    "id": e.id, "handler": e.handler, "title": e.title,
                    "enabled": e.enabled, "bucket": e.bucket,
                    "poll_cadence": e.poll_cadence,
                    "discovery": {"type": e.discovery.type, "url": e.discovery.url},
                    "release": e.release_schedule.frequency if e.release_schedule else None,
                    "factor_tags": e.factor_tags, "entity_tags": e.entity_tags,
                }
                for e in t.endpoints
            ],
        }

    @app.post("/admin/ingest")
    def admin_ingest(req: IngestRequest, _: Any = Depends(admin_dep)):
        return _submit_exclusive("ingest", req.model_dump(), ingest_runner)

    # ── curation (vocabulary + maintenance scripts) ─────────────────────────

    def _vocab_response(v: dict) -> dict:
        return {
            "factor_tags": sorted(v["factor_tags"]),
            "entity_tags": sorted(v["entity_tags"]),
            "doc_types": sorted(v["doc_types"]),
            "aliases": v["aliases"],
        }

    @app.get("/admin/vocabulary")
    def admin_vocabulary(_: Any = Depends(admin_dep)):
        from ingest.common.config import load_vocabulary
        return _vocab_response(load_vocabulary())

    @app.put("/admin/vocabulary")
    def admin_vocabulary_edit(req: VocabularyUpdate, _: Any = Depends(admin_dep)):
        """§E: edit the controlled vocabulary (factor_tags / entity_tags / aliases).
        Persists to vocabulary.yaml and invalidates the cache; ``doc_types`` (grouped)
        stay view-only here. Only provided fields are changed."""
        from ingest.common.config import save_vocabulary_fields
        updated = save_vocabulary_fields(
            factor_tags=req.factor_tags, entity_tags=req.entity_tags, aliases=req.aliases)
        return _vocab_response(updated)

    @app.post("/admin/curate")
    def admin_curate(req: CurateRequest, _: Any = Depends(admin_dep)):
        if req.task not in CURATION_TASKS:
            raise HTTPException(status_code=400,
                                detail=f"unknown task; expected one of {CURATION_TASKS}")
        return _submit_exclusive("curate", req.model_dump(), curation_runner)
