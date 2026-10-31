"""FastAPI service for Macro RAG (ARag review §2.3 serving tier).

Endpoints:
  GET  /healthz              liveness + index manifest summary
  POST /query                synchronous cited answer (JSON)
  POST /query/stream         same, as Server-Sent Events (retrieved -> answer -> done)
  POST /factors              ranked, cited macro risk factors for a portfolio/theme
  GET  /citations/{chunk_id} fetch a cited chunk's text + provenance (opaque id)
  POST /citations            batch provenance lookup (many chunk_ids -> texts + misses)
  POST /feedback             thumbs +/- with optional note (logged; reconciled into
                             golden CANDIDATES via /admin/feedback/reconcile, review-gated)
  GET  /corpus/*             facet-driven corpus browse + series catalog (api.corpus)
  GET  /vocabulary           controlled facet vocabulary for filter construction
  GET  /synthesis/schema     shock-field catalog (units + sign conventions)
  GET  /admin/*              read-only system/maintenance surface (see api.admin)

The pipeline is built lazily on first request via the injected factory, so the
heavy models load on first use, not at import — and tests inject a fake pipeline.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

from llm.usage import track_usage
from utils.alerting import build_alerter
from utils.config import RagSettings, load_settings
from utils.costguard import BudgetExceeded, build_cost_guard
from utils.locks import build_exclusive_lock
from utils.logging import get_logger
from utils.metrics import observe_request, render_metrics
from utils.observability import init_observability
from utils.ratelimit import build_rate_limiter
from utils.schema import MetaFilter

from .admin import add_admin_routes
from .corpus import add_corpus_routes
from .discovery import add_discovery_routes
from .jobs import Job, JobManager
from .jobstore import build_job_store
from .models import (
    CitationsRequest,
    FactorRequest,
    FeedbackRequest,
    QueryRequest,
    cache_opts,
    citation_to_dict,
    factors_to_dict,
    resolve_citations,
    result_to_dict,
    synthesis_to_dict,
    usage_to_dict,
    validate_shock_directions,
    validate_shock_fields,
)

log = get_logger(__name__)


def _default_index_runner(settings: RagSettings) -> Callable[[Job], dict]:
    """Index-build job body: drives pipelines.index_pipeline.run, or run_incremental
    for an incremental request (lazy imports so the heavy embedder/Qdrant stack only
    loads when a build is actually run)."""
    def run(job: Job) -> dict:
        if job.params.get("incremental"):
            dry_run = bool(job.params.get("dry_run"))
            job.log(f"starting incremental index refresh{' (dry run)' if dry_run else ''}")
            from pipelines.index_pipeline import run_incremental
            stats = run_incremental(settings, dry_run=dry_run,
                                    should_cancel=job.check_cancel)
            summary = (f"scanned {stats['scanned']} chunks: {stats['new']} new, "
                       f"{stats['changed']} changed, {stats['stale']} departed")
            if not dry_run:
                summary += f", {stats['indexed']} embedded, {stats['deleted']} deleted"
            job.log(summary)
            return stats
        append = bool(job.params.get("append"))
        job.log(f"starting index build ({'append' if append else 'recreate'})")
        from pipelines.index_pipeline import run as index_run
        report = index_run(settings, recreate=not append,
                           should_cancel=job.check_cancel)
        job.log(f"indexed {report['chunks']} chunks, "
                f"{report['observations']} observations")
        return report
    return run


def _default_eval_runner(settings: RagSettings,
                         pipe: Callable[[], Any]) -> Callable[[Job], dict]:
    """Eval job body: run retrieval over the golden set and compare to the drift
    baseline. Uses the (lazily built) retriever; heavy only when actually run."""
    def run(job: Job) -> dict:
        k = int(job.params.get("k", 8))
        from eval.drift import detect_drift, load_baseline
        from eval.harness import evaluate_retrieval, load_golden
        golden = load_golden(settings.golden_path)
        job.log(f"evaluating {len(golden)} golden questions @k={k}")
        report = evaluate_retrieval(pipe().retriever, golden, k=k,
                                    should_cancel=job.check_cancel)
        summary = report["summary"]
        # §B: optional LLM-judge answer-quality pass — folds system-wide
        # faithfulness/relevancy into the summary (so drift can gate on them too).
        judge_rows = None
        if bool(job.params.get("judge", settings.eval.judge)):
            from eval.harness import evaluate_answers
            job.log("running LLM-judge answer-quality pass...")
            jr = evaluate_answers(pipe(), golden, should_cancel=job.check_cancel)
            summary = {**summary, **jr["summary"]}
            judge_rows = jr["rows"]
        job.log("summary: " + ", ".join(f"{m}={v:.3f}" for m, v in summary.items()))
        baseline = load_baseline(settings.eval_baseline_path)
        regressions = (detect_drift(summary, baseline, tolerance=settings.eval.tolerance)
                       if baseline else [])
        if regressions:
            job.log(f"{len(regressions)} regression(s) vs baseline")
            # §K: enforce the gate — alert (out-of-band) and optionally fail the job.
            if settings.eval.alert_on_regression:
                build_alerter(settings).send(
                    f"eval regression: {len(regressions)} metric(s) dropped vs baseline",
                    level="critical",
                    metrics=", ".join(r["metric"] for r in regressions))
        _append_eval_history(settings, {
            "ts": datetime.now(UTC).isoformat(),
            "k": k, "summary": summary, "n_questions": len(golden),
            "n_regressions": len(regressions),
        })
        if regressions and settings.eval.fail_on_regression:
            raise RuntimeError(
                f"{len(regressions)} eval regression(s) vs baseline "
                "(eval.fail_on_regression is on)")
        return {"k": k, "summary": summary, "rows": report["rows"],
                "judge_rows": judge_rows,
                "baseline": baseline, "regressions": regressions,
                "regressed": bool(regressions)}
    return run


def _default_selfcheck_runner(settings: RagSettings,
                              pipe: Callable[[], Any]) -> Callable[[Job], dict]:
    """Self-check job body: differential-test the answer path against the stored
    numeric answer key (eval/selfcheck.py — Loops 1 + 5).

    Deterministic: no golden file, no LLM judge. Cases are generated from the
    time-series store at run time, so the harness grows with the corpus."""
    def run(job: Job) -> dict:
        from eval.selfcheck import run_selfcheck
        p = pipe()
        store = getattr(p, "timeseries", None)
        if store is None or not store.count():
            raise RuntimeError(
                "self-check needs a built time-series store — run an index first")
        report = run_selfcheck(
            store, lambda q: p.answer(q).answer,
            per_group=int(job.params.get("per_group", 2)),
            max_cases=int(job.params.get("max_cases", 100)),
            per_kind=int(job.params.get("per_kind", 4)),
            sources=job.params.get("sources") or None,
            should_cancel=job.check_cancel, on_progress=job.log)
        return report.to_dict()
    return run


def _default_backtest_runner(settings: RagSettings,
                             pipe: Callable[[], Any]) -> Callable[[Job], dict]:
    """Realized-episode backtest job (eval/episodes.py — Loop 4).

    Scores synthesized vectors against what markets actually did, in two modes
    whose metrics are kept apart: ``grounding_fidelity`` (evidence visible) and
    ``ex_ante_skill`` (retrieval bounded to what existed at the time)."""
    def run(job: Job) -> dict:
        from eval.episodes import EPISODES, EPISODES_BY_KEY, MODES, run_episode_backtest
        p = pipe()
        store = getattr(p, "timeseries", None)
        if store is None or not store.count():
            raise RuntimeError(
                "backtest needs a built time-series store — run an index first")

        keys = job.params.get("episodes") or None
        episodes = ([EPISODES_BY_KEY[k] for k in keys if k in EPISODES_BY_KEY]
                    if keys else list(EPISODES))
        modes = [m for m in (job.params.get("modes") or MODES) if m in MODES]

        def synth(question, *, horizon_days=None, base_filter=None):
            return p.synthesize(question, horizon_days=horizon_days,
                                base_filter=base_filter).shock_vector

        return run_episode_backtest(store, synth, episodes=episodes, modes=modes,
                                    should_cancel=job.check_cancel,
                                    on_progress=job.log)
    return run


def _append_eval_history(settings: RagSettings, entry: dict) -> None:
    """Append one eval run's summary to the history log (best-effort; a write
    failure must not fail the eval job itself)."""
    try:
        path = settings.eval_history_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:  # pragma: no cover
        log.warning("eval history write failed: %s", exc)


def _default_ingest_runner() -> Callable[[Job], dict]:
    """Ingest job body: run a tier (optionally a subset of endpoints) via the
    ingest package. Lazy import keeps the heavy handler stack out of app import."""
    def run(job: Job) -> dict:
        p = job.params
        tier = p["tier"]
        endpoints = p.get("endpoints") or None
        from ingest import TIER_INGESTORS
        from ingest.common import build_context
        job.check_cancel()      # honour a cancel requested before work starts
        if tier not in TIER_INGESTORS:
            raise ValueError(f"unknown tier '{tier}'")
        job.log(f"ingesting {tier}" +
                (f" endpoints={endpoints}" if endpoints else " (all enabled)"))
        ctx = build_context(tier, refresh=bool(p.get("refresh")))
        try:
            ingestor = TIER_INGESTORS[tier](ctx)
            report = ingestor.run(endpoint_ids=endpoints,
                                  limit_per_endpoint=p.get("limit"))
        finally:
            ctx.client.close()
            ctx.cache.close()
        job.log(str(report))
        errors = getattr(report, "errors", None)
        return {"summary": str(report),
                "parsed": getattr(report, "parsed", None),
                "n_errors": len(errors) if isinstance(errors, (list, tuple)) else errors}
    return run


def _default_curation_runner() -> Callable[[Job], dict]:
    """Curation job body: run a maintenance script under scripts/ and stream its
    printed report into the job log. Scripts are loaded by path on demand."""
    # task -> (script filename, argv builder from job params)
    scripts: dict[str, tuple[str, Callable[[dict], list[str]]]] = {
        "normalize-vocab": ("normalize_vocab.py",
                            lambda p: ["--dry-run"] if p.get("dry_run") else []),
        "rechunk": ("rechunk_processed.py",
                    lambda p: ["--dry-run"] if p.get("dry_run") else []),
        "reextract-sep": ("reextract_sep.py",
                          lambda p: ["--dry-run"] if p.get("dry_run") else []),
        "reextract-dfast": ("reextract_dfast.py",
                            lambda p: ["--strip-only"] if p.get("strip_only") else []),
    }

    def run(job: Job) -> dict:
        import contextlib
        import importlib.util
        import io

        from utils.config import REPO_ROOT

        task = job.params["task"]
        job.check_cancel()      # honour a cancel requested before work starts
        if task not in scripts:
            raise ValueError(f"unknown curation task '{task}'")
        fname, argv_of = scripts[task]
        argv = argv_of(job.params)
        job.log(f"running {task} {argv or '(apply)'}")

        path = REPO_ROOT / "scripts" / fname
        spec = importlib.util.spec_from_file_location(f"curation_{task}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # union-attr is disabled for api.app in mypy.ini

        class _LogWriter(io.TextIOBase):
            def write(self, s: str) -> int:
                for line in s.splitlines():
                    if line.strip():
                        job.log(line)
                return len(s)

        # _LogWriter is a minimal write/flush sink, not a full TextIO
        with contextlib.redirect_stdout(_LogWriter()):   # type: ignore[type-var]
            rc = module.main(argv)
        job.log(f"exit code {rc}")
        return {"task": task, "args": argv, "exit_code": rc}
    return run


def create_app(pipeline_factory: Callable[[], object],
               settings: RagSettings | None = None,
               *,
               jobs: JobManager | None = None,
               index_runner: Callable[[Job], object] | None = None,
               eval_runner: Callable[[Job], object] | None = None,
               selfcheck_runner: Callable[[Job], dict | None] | None = None,
               backtest_runner: Callable[[Job], dict | None] | None = None,
               ingest_runner: Callable[[Job], object] | None = None,
               curation_runner: Callable[[Job], object] | None = None,
               cost_guard: object | None = None) -> FastAPI:
    app = FastAPI(title="Macro RAG", version="1.0.0")
    settings = settings or load_settings()
    # §I: the exclusive-job lock + job-record store are Redis-backed when
    # jobs.backend=redis (cross-replica + restart-durable), else in-process.
    jobs = jobs or JobManager(
        lock=build_exclusive_lock(settings),
        store=build_job_store(settings),
        idempotency_ttl_s=settings.jobs.idempotency_ttl_s)
    index_runner = index_runner or _default_index_runner(settings)
    ingest_runner = ingest_runner or _default_ingest_runner()
    curation_runner = curation_runner or _default_curation_runner()
    init_observability(settings.observability)

    if settings.observability.metrics_enabled:
        import time as _time

        @app.middleware("http")
        async def _metrics_middleware(request, call_next):
            """Record REAL request wall-clock latency + status counts (§J) — not the
            summed trace timings the admin roll-up used. Route template (not the raw
            path) is the label, so path params don't explode label cardinality."""
            start = _time.perf_counter()
            response = await call_next(request)
            route = request.scope.get("route")
            route_label = getattr(route, "path", None) or "unmatched"
            observe_request(request.method, route_label, response.status_code,
                            _time.perf_counter() - start)
            return response

        @app.get("/metrics")
        def metrics_endpoint():
            """Prometheus scrape target (per-replica counters/histograms)."""
            return PlainTextResponse(render_metrics(),
                                     media_type="text/plain; version=0.0.4; charset=utf-8")

    state: dict = {}
    # Rate limiter: in-process token bucket by default; a Redis shared counter when
    # auth.rate_limit_backend=redis, so the per-key cap is global across replicas (§I).
    rate_limiter = build_rate_limiter(settings)
    # §K governance: out-of-band alerting + hard daily spend ceilings (both off by
    # default). The cost guard shares the alerter for per-request cost alerts.
    alerter = build_alerter(settings)
    cost_guard = cost_guard or build_cost_guard(settings, alerter=alerter)

    # §L CORS: only add headers when origins are configured (else same-origin only).
    if settings.api.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.api.cors_allow_origins,
            allow_methods=["*"], allow_headers=["*"])

    # §L admission control: bound concurrent in-flight pipeline requests so the CPU
    # rerank bottleneck can't queue unboundedly under load (503 when saturated).
    _sema = (threading.BoundedSemaphore(settings.api.max_concurrency)
             if settings.api.max_concurrency > 0 else None)
    _admit_wait = settings.api.max_concurrency_wait_s

    def _admission_acquire() -> bool:
        if _sema is None:
            return False                    # nothing to release
        got = (_sema.acquire(timeout=_admit_wait) if _admit_wait > 0
               else _sema.acquire(blocking=False))
        if not got:
            raise HTTPException(status_code=503, detail="server busy; retry shortly")
        return True

    def _admission_release(held: bool) -> None:
        if held and _sema is not None:
            _sema.release()

    @contextlib.contextmanager
    def _admit():
        held = _admission_acquire()
        try:
            yield
        finally:
            _admission_release(held)

    def pipe():
        if "p" not in state:
            log.info("building query pipeline (first request)...")
            state["p"] = pipeline_factory()
        return state["p"]

    eval_runner_ = eval_runner or _default_eval_runner(settings, pipe)
    selfcheck_runner_ = selfcheck_runner or _default_selfcheck_runner(settings, pipe)
    backtest_runner_ = backtest_runner or _default_backtest_runner(settings, pipe)

    def _rate_ok(api_key: str) -> bool:
        return rate_limiter.allow(api_key, settings.auth.rate_limit_rpm)

    def auth(x_api_key: str | None = Header(default=None)) -> str | None:
        """Return the caller's principal — the identity used for auth, rate
        limiting and cost attribution (None when auth is disabled). NOT a
        data-isolation key; the corpus is shared."""
        if not settings.auth.enabled:
            return None
        if not x_api_key or x_api_key not in settings.auth.api_keys:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        if not _rate_ok(x_api_key):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        return settings.auth.api_keys[x_api_key]

    def admin_auth(x_api_key: str | None = Header(default=None)) -> str | None:
        """Gate the /admin surface: a valid key that is also admin-authorised.
        Open when auth is disabled (dev), mirroring the rest of the API."""
        if not settings.auth.enabled:
            return None
        if not x_api_key or x_api_key not in settings.auth.api_keys:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        if x_api_key not in settings.auth.admin_api_keys:
            raise HTTPException(status_code=403, detail="admin privileges required")
        if not _rate_ok(x_api_key):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        return settings.auth.api_keys[x_api_key]

    def _validated_fields(fields: list[str]) -> list[str] | None:
        """Validate requested shock fields; map the shared validator's ValueError
        to HTTP 400 so the consumer can fix it (valid set: GET /synthesis/schema)."""
        try:
            return validate_shock_fields(fields)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    def _validated_directions(directions: dict[str, str]) -> dict[str, str] | None:
        """Validate requested field directions; unknown names -> HTTP 400."""
        try:
            return validate_shock_directions(directions)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    def _run_guarded(principal: str | None, thunk):
        """Wrap a billable request with §K cost governance: pre-request admission
        (402 when a daily ceiling is already hit) + post-request spend accounting.
        A no-op passthrough when cost caps are disabled, so default behaviour and
        the existing per-op usage logging are unchanged."""
        if cost_guard.enabled:
            try:
                cost_guard.check(principal)
            except BudgetExceeded as exc:
                raise HTTPException(status_code=402, detail=str(exc))
        with _admit():                      # §L: bounded concurrency (503 when saturated)
            if not cost_guard.enabled:
                return thunk()
            with track_usage("api-request") as agg:
                out = thunk()
            cost_guard.record(principal, agg.cost_usd if agg else 0.0)
            return out

    def _with_principal(base: MetaFilter | None,
                        principal: str | None) -> MetaFilter | None:
        # Stamps the caller's principal onto the retrieval filter's DORMANT
        # ``tenant_id`` hook. This isolates nothing today: no ingest path writes
        # ``tenant_id``, so every chunk is unpartitioned and shared. It becomes a
        # real boundary only if a private per-principal ingest path is added, at
        # which point the hook already discriminates correctly. See Production Hardening §A.
        if not principal:
            return base
        mf = base or MetaFilter()
        mf.tenant_id = principal
        return mf

    @app.get("/healthz")
    def healthz():
        from utils.manifest import read_manifest
        from utils.objectstore import manifest_store
        manifest = read_manifest(manifest_store(settings))
        return {"status": "ok", "built": state.get("p") is not None,
                "index": manifest}

    @app.post("/query")
    def query(req: QueryRequest, principal: str | None = Depends(auth)):
        base = _with_principal(req.filters.to_meta() if req.filters else None, principal)
        p = pipe()

        def _do():
            if req.mode == "synthesize":      # AITrade scenario-synthesis (spec §3)
                opts = req.synthesis
                fields = _validated_fields(opts.fields) if opts else None
                directions = _validated_directions(opts.directions) if opts else None
                syn = p.synthesize(req.question, base_filter=base, fields=fields,
                                   horizon_days=opts.horizon_days if opts else None,
                                   scenario=opts.scenario if opts else None,
                                   directions=directions, **cache_opts(req.no_cache))
                return synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
                                         diagnostics=syn.diagnostics)
            result = p.answer(req.question, base_filter=base, **cache_opts(req.no_cache))
            return result_to_dict(result.answer, result.chunks, result.analysis,
                                  cached=result.cached)

        # The request's model-call cost goes back to the caller (spec
        # answer-usage-cost); _run_guarded's own aggregate joins this one.
        with track_usage("api-query") as agg:
            body = _run_guarded(principal, _do)
        body["usage"] = usage_to_dict(agg, cached=bool(body.get("cached")))
        return body

    @app.post("/query/stream")
    def query_stream(req: QueryRequest, principal: str | None = Depends(auth)):
        base = _with_principal(req.filters.to_meta() if req.filters else None, principal)
        if cost_guard.enabled:                 # §K admission before we start streaming
            try:
                cost_guard.check(principal)
            except BudgetExceeded as exc:
                raise HTTPException(status_code=402, detail=str(exc))
        held = _admission_acquire()            # §L: 503 cleanly before the response starts

        def events():
            try:
                yield _sse("status", {"stage": "retrieving"})
                with track_usage("api-stream") as agg:
                    result = pipe().answer(req.question, base_filter=base,
                                           **cache_opts(req.no_cache))
                if cost_guard.enabled:
                    cost_guard.record(principal, agg.cost_usd)
                yield _sse("retrieved", {"n_chunks": len(result.chunks),
                                         "route": result.analysis.route,
                                         "cached": result.cached})
                body = result_to_dict(result.answer, result.chunks, result.analysis,
                                      cached=result.cached)
                body["usage"] = usage_to_dict(agg, cached=result.cached)
                yield _sse("answer", body)
                yield _sse("done", {})
            finally:
                _admission_release(held)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/factors")
    def factors(req: FactorRequest, principal: str | None = Depends(auth)):
        base = _with_principal(req.filters.to_meta() if req.filters else None, principal)
        return _run_guarded(principal, lambda: factors_to_dict(
            pipe().discover_factors(req.portfolio, base_filter=base, top_k=req.top_k)))

    @app.get("/citations/{chunk_id}")
    def citation(chunk_id: str, principal: str | None = Depends(auth)):
        store = pipe().retriever.store
        got = store.get_by_chunk_ids([chunk_id])
        if not got:
            raise HTTPException(status_code=404, detail="chunk not found")
        return citation_to_dict(got[0])

    @app.post("/citations")
    def citations(req: CitationsRequest, principal: str | None = Depends(auth)):
        return resolve_citations(pipe().retriever.store, req.chunk_ids)

    @app.post("/feedback")
    def feedback(req: FeedbackRequest, principal: str | None = Depends(auth)):
        record = {"ts": datetime.now(UTC).isoformat(),
                  "principal": principal, **req.model_dump()}
        path = settings.feedback_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"status": "recorded"}

    add_admin_routes(app, settings=settings, pipe=pipe,
                     peek=lambda: state.get("p"), admin_dep=admin_auth,
                     jobs=jobs, index_runner=index_runner, eval_runner=eval_runner_,
                     selfcheck_runner=selfcheck_runner_,
                     backtest_runner=backtest_runner_,
                     ingest_runner=ingest_runner, curation_runner=curation_runner)
    add_corpus_routes(app, settings=settings, auth_dep=auth)
    add_discovery_routes(app, settings=settings, auth_dep=auth)
    return app


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
