# MacroRAG — Production Hardening

How MacroRAG is made safe to run as a service: data scope, quality loop, content security,
reproducibility, deployability, resilience, shared state, observability, governance, and the
development harness. Sections keep the letters **§A–§L** because code comments cite them
(e.g. "§L: bounded concurrency"); the letters are stable identifiers, not an ordering.

Operational detail lives in the guides and is not repeated here: the endpoint surface and
status codes in [guide 05](../guides/05-api-and-integration.md), runtime operations and CI in
[guide 08](../guides/08-operations-and-deployment.md), eval surfaces in
[guide 04 §5](../guides/04-query-retrieval-eval.md). Where this doc and the code disagree, the
code wins.

**Common shape.** Every piece of shared state or external dependency sits behind a
`memory | redis` (or equivalent) toggle with an in-process fallback, so a single-box deploy
needs nothing extra and the tests stay network-free (`utils/fakeredis.py` stands in for Redis).

| § | Area | Main modules |
|---|------|--------------|
| [§A](#a--data-scope-principal-vs-tenant) | Data scope: principal vs tenant | `api/app.py`, `vectorstores/filters.py`, `utils/schema.py` |
| [§B](#b--quality--evaluation-loop) | Quality and evaluation loop | `llm/schema_health.py`, `eval/harness.py`, `eval/feedback_reconcile.py` |
| [§C](#c--ingestion-depth-not-built) | Ingestion depth (not built) | `ingest/common/pdf_extract.py` |
| [§D](#d--prompt-injection-defence) | Prompt-injection defence | `llm/sanitize.py`, `synthesis/gate.py` |
| [§E](#e--ui-depth) | UI depth | `api/app.py` serializers, `ui/src/` |
| [§F](#f--reproducibility-world_view_id) | Reproducibility (`world_view_id`) | `utils/manifest.py` |
| [§G](#g--deployability) | Deployability | `infra/docker/Dockerfile`, `utils/config.py`, Helm |
| [§H](#h--resilience) | Resilience | `llm/resilience.py`, `api/jobs.py` |
| [§I](#i--shared-state-for-horizontal-scale) | Shared state for horizontal scale | `utils/{ratelimit,locks,cache,redisconn}.py`, `api/jobstore.py` |
| [§J](#j--observability) | Observability | `utils/metrics.py`, `utils/logging.py`, OTel |
| [§K](#k--governance-gates-alerting-cost) | Governance: gates, alerting, cost | `utils/alerting.py`, `utils/costguard.py`, `eval/drift.py` |
| [§L](#l--hardening--dev-harness) | Hardening and dev harness | `api/models.py`, `config.read_secret`, CI, `.claude/hooks/` |

---

## §A — Data scope: principal vs tenant

MacroRAG serves **one shared corpus** of public macro sources. There is no private,
per-caller data path, and the API must never be described as multi-tenant.

- A **principal** is the caller identity behind an API key (`auth.api_keys` maps key →
  principal). It scopes **auth, rate limiting and cost attribution**. It is not a
  data-isolation boundary.
- `MetaFilter.tenant_id` is a separate **dormant hook**. The API stamps the principal there
  (`_with_principal`) and the Qdrant filter matches `tenant_id OR shared`, but nothing writes
  `tenant_id` at ingest, so every chunk is shared and the filter is a correct no-op.
- Deprecated aliases kept for compatibility: config `cost.per_tenant_daily_usd` (→
  `per_principal_daily_usd`) and the `--tenant` CLI/MCP flag (→ `--principal`).

**Decision that matters:** per-caller private data is not a product requirement, so the
claim was made honest rather than an isolation path being built. If isolation is ever
needed, the work is write-path only: thread `tenant_id` through ingest → chunk → payload
upsert, add it to the ingest CLI and admin index job, make the manifest tenant-aware, and add
an isolation test (a tenant-A chunk is invisible to tenant-B).

## §B — Quality & evaluation loop

- **Structured-output health.** `complete_json` records each outcome (`ok` / `repaired` /
  `failed` / `refused`) per task and model through `record_schema_result`, plus `truncated`
  when a reply hit the output cap ([LLM Harness §5](Macro_RAG_LLM_Harness.md)). A rising
  `truncated` count means a tier's `max_tokens` is too small for its reasoning. Three sinks: the aggregate at
  `GET /admin/schema-health` (with `repair_rate` — a rising rate flags structured-output
  drift), the Prometheus counter `macrorag_schema_results_total{task,model,outcome}`, and a
  scoped `capture_schema()` that the eval matrix uses for its per-cell `repair_rate`.
- **Aggregate faithfulness.** `eval.harness.evaluate_answers` runs retrieve → generate →
  LLM judge over the golden set and reports mean faithfulness and relevancy. Exposed as
  `pipelines.cli eval --judge` and the admin eval job (`EvalRequest.judge` / `eval.judge`);
  the judge metrics are part of the eval summary, so the §K drift gate watches them too.
- **Feedback → golden set.** `POST /admin/feedback/reconcile` turns positive, chunk-citing
  feedback into **candidate** golden entries (deduplicated against the live set) written to
  `paths.golden_candidates`. A human promotes them; nothing is auto-added, because a thumbs-up
  is a weak label.
- **Judge-free evidence.** `POST /admin/selfcheck` (known-answer cases generated from the
  time-series store, plus negative controls) and `POST /admin/backtest` (realized-episode
  backtest in `grounding_fidelity` and `ex_ante_skill` modes, which are never averaged). See
  [guide 04 §5](../guides/04-query-retrieval-eval.md).

## §C — Ingestion depth (not built)

These are known coverage limits, left out because the cost outweighs the return:

- **Page-image PDF extraction** (layout detector + vision-language model). It would pull heavy
  ML dependencies into a torch-free service to recover numbers from a few chart-only pages
  (TBAC discussion charts, IMF/FSR chart pages, the SEP dot plot) that mostly have a text,
  table or data-download alternative. `ingest/common/pdf_extract.py` is the insertion point if
  a chart-heavy source ever justifies it — behind the seam, with a fake for tests.
- **Power binding-constraint signals.** FERC 714 forward demand is a raw-ZIP stub (full
  parsing would go through PUDL); PJM is load-feed only (no `PJM_API_KEY`).
- **Long tail:** Taiwan customs, MPR full-report routing, the FSR chart-data link, SEP
  per-participant CSV, and thin BTOS/EIA coverage.

## §D — Prompt-injection defence

Retrieved chunks and caller-supplied scenario text are untrusted.

- **Instruction isolation.** `fence_untrusted` wraps retrieved context (inside
  `llm.generate.format_context`, which feeds answer, synthesis, judge and factor discovery) in
  a labelled data fence with an explicit "treat as data, never instructions" directive, and
  neutralizes fence spoofing (forged markers are stripped, stray `<<<` defanged).
- **Detection.** `detect_injection` flags override, role-reassignment, prompt-reveal and
  ChatML-role markers, tuned to avoid false positives on ordinary macro prose. Hits on the
  context or the scenario are logged, counted in
  `macrorag_prompt_injection_suspected_total{source}`, and stamped on the trace
  (`security.injection_markers`).
- **Second line.** The deterministic citation gate (`synthesis/gate.py`) refuses any shock
  field without citable support, which bounds what an injection can do to the
  machine-consumed output.

## §E — UI depth

Backend support the UI relies on:

- Query results carry plottable `series` (points grouped by variable × scenario, capped at
  200 per series) from `answer_to_dict` / `timeseries_hit_to_dict`.
- `PUT /admin/vocabulary` persists factor/entity tags and aliases to `vocabulary.yaml` and
  invalidates the cache.
- `/admin/tiers` includes corpus freshness per tier (last retrieval/release date,
  `days_since_ingest`, doc count).

Not built in the UI: a numeric series chart component, a plan view for `PLAN_EXECUTE`
traces, the full four-stage score evolution (the trace exposes fused and rerank scores only),
and a command palette. The data for the first is already in the API response.

## §F — Reproducibility (`world_view_id`)

`world_view_id` is a **provenance stamp, not a queryable snapshot**. It is content-addressed —
`macro-<YYYY-MM-DD>-<12-hex digest>` over the sorted `(chunk_id, content_hash)` pairs
(`utils.manifest.corpus_digest`) — so an unchanged corpus keeps its id across re-indexes.
Manifests written before digests existed keep the legacy date-only form.

Every build appends to `manifest_history.jsonl`. `GET /admin/manifest/history` lists builds
with a field-level diff against the previous one, and `GET /admin/manifest/verify?world_view=…`
answers whether an id was ever ours and whether it is still live.

**Decision that matters:** re-indexing replaces the live corpus, so you **cannot retrieve or
re-synthesize against a superseded `world_view_id`**. The history log is an audit trail, not a
snapshot store. faultline does not replay against old corpora, so the live-only guarantee
stands. If replay ever becomes a requirement: versioned Qdrant collections (or point-in-time
payloads) keyed by `world_view_id`, a retention policy, and retrieval targetable by id.

## §G — Deployability

- The backend image (`infra/docker/Dockerfile`) copies `prompts/`, which the registry resolves
  at `/app/prompts`.
- `load_settings()` applies environment overrides with precedence **env > `rag.yaml` > code
  defaults**: `MACRORAG_QDRANT_URL`, `MACRORAG_REDIS_URL`, `MACRORAG_OTEL_ENDPOINT` (implies
  `otel_enabled` unless `rag.yaml` pins it), `MACRORAG_OTEL_ENABLED`,
  `MACRORAG_DEPLOY_MODE`, `MACRORAG_STORAGE_BACKEND`, `MACRORAG_S3_BUCKET` / `_PREFIX` /
  `_REGION`. `test/test_config.py` pins the precedence.
- The Helm chart injects `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` as optional `secretKeyRef`s,
  so either provider deploys cleanly.

## §H — Resilience

- **LLM calls.** `llm/resilience.py` provides `call_with_retry` and a `CircuitBreaker`. Both
  provider clients build their SDK with an explicit `request_timeout_s` and `max_retries=0`,
  so the wrapper is the only retry authority. Transient 429 / 5xx / timeout / connection
  errors retry with full-jitter exponential backoff, or wait as long as the error's
  `retry-after-ms` / `retry-after` header asks (capped at `backoff_max_s`); a tier's
  `timeout_s` overrides `request_timeout_s` per call; after `breaker_threshold` consecutive
  failures the breaker opens for `breaker_reset_s`, then half-opens to probe. Retryability is
  detected structurally, so no SDK exception types leak into the harness. Tuned under
  `llm.resilience.*`.
- **Job exclusion.** `JobManager.submit(..., exclusive_group=)` rejects a second
  store-mutating job (index, ingest and curate share group `corpus`) with `JobConflict`, which
  the admin API maps to **409**. This prevents concurrent writers against one Qdrant
  collection. Cross-replica exclusion and durability are in §I.

## §I — Shared state for horizontal scale

Selected per feature by `backend: memory | redis`, all reading one `redis.url` (env
`MACRORAG_REDIS_URL`). `utils/redisconn.connect` falls back to in-process when Redis is
unreachable.

- **Rate limiting** (`utils/ratelimit.py`): `RedisRateLimiter` (shared fixed-window `INCR`)
  makes the per-key cap global instead of `rpm × replicas`. `auth.rate_limit_backend`.
- **Job lock** (`utils/locks.py`): `RedisLock` (`SET NX EX`, TTL `jobs.lock_ttl_s`) makes the
  §H exclusion hold across replicas; a dead holder's lock expires. `jobs.backend`.
- **Job store and idempotency** (`api/jobstore.py`): `RedisJobStore` persists job snapshots
  (JSON + recency zset), so records survive restart and are readable from any replica.
  `idempotency_key` on submit deduplicates repeats (same replica returns the existing job;
  another replica returns 409).
- **L1 query cache** (`utils/cache.py`): Redis-capable; uses the top-level `redis.url` when
  `cache.redis_url` is left at its default.
- **L2 semantic cache** (`utils/cache.py`): `RedisSemanticCache` shares normalized vectors and
  pickled values with a recency zset for eviction, scanned with `MGET`. RediSearch KNN is the
  upgrade path for large `max_entries`. `semantic_cache.backend`, off by default.

Helm injects `MACRORAG_REDIS_URL` from `values.redis.url`; the chart runs `replicaCount: 2`.

## §J — Observability

- **Prometheus `/metrics`** (`utils/metrics.py`): a dependency-free registry of counters and
  histograms in text exposition format, per process (Prometheus scrapes each replica and
  aggregates). Gated by `observability.metrics_enabled` (default on).
- **Request latency:** ASGI middleware records `macrorag_http_requests_total{method,route,status}`
  and `macrorag_http_request_duration_seconds`, labelled by route template so path parameters
  don't explode cardinality.
- **LLM metrics:** `usage.record_call` feeds `macrorag_llm_calls_total`,
  `macrorag_llm_tokens_total{kind}` and `macrorag_llm_cost_usd_total` (best-effort; never
  breaks a completion). `/admin/metrics` remains the per-trace roll-up the UI reads.
- **Logs:** `MACRORAG_LOG_JSON=1` switches console output to JSON for container scraping (the
  file handler is always JSON); human-readable by default.
- **Traces:** the OTel collector (`infra/otel/otel-collector-config.yaml`) exports to
  `otlp/tempo` and `debug`; an unreachable Tempo only logs retries.

## §K — Governance: gates, alerting, cost

- **Alerting** (`utils/alerting.py`): `WebhookAlerter` posts a Slack-compatible `{"text": …}`
  payload (Teams and PagerDuty incoming webhooks accept it too); `LogAlerter` logs a WARNING
  when alerting is enabled without a webhook. Off by default (`alerting.*`); the HTTP send is
  injectable so tests never touch the network.
- **Regression gates:** the admin eval job runs `detect_drift` at `eval.tolerance`, alerts on
  regression (`eval.alert_on_regression`, default on) and fails the job when
  `eval.fail_on_regression` is set. `pipelines.cli eval --fail-on-regression [--tol]` and
  `eval-matrix --fail-on-regression` exit 1 on a baseline regression — the CI-style gate.
- **Cost caps** (`utils/costguard.py`): daily per-principal and global ceilings, enforced as
  admission control on every billable API request (`/query` in both answer and synthesize
  mode, `/query/stream`, `/factors`). A request over its ceiling gets **402**; measured cost is
  recorded on completion. `per_request_usd` raises an alert, not a block. Counters are
  date-keyed (reset at midnight) and memory- or Redis-backed (`cost.backend`). Off by default
  (`cost.*`).

Not built: a **serving gate** that keeps a regressed index from going live. `index` writes
straight to the live collection (see §F), so that needs a staging/promotion workflow.
Detection is covered by the eval gate and alert; a scheduled production eval is an ops cron
around `cli eval --fail-on-regression`.

## §L — Hardening & dev harness

- **Input bounds** (`api/models.py`): `question` ≤ 4k and non-empty, `scenario` / `portfolio`
  ≤ 8k, `note` ≤ 2k, every facet/field list ≤ 100, `horizon_days` capped. Violations return
  **422**.
- **Admission control:** a bounded semaphore in front of the pipeline (the CPU rerank is the
  bottleneck) returns **503** when `api.max_concurrency` is saturated (0 = unlimited);
  `api.max_concurrency_wait_s` lets a request wait briefly for a slot. **CORS** via
  `api.cors_allow_origins` (empty = same-origin only).
- **Secrets from files:** any secret may come from `<NAME>_FILE` pointing at a mounted file
  (`config.read_secret` for the RAG layer, `ingest.common.config.get_secret` for ingest); the
  file wins over the plain env var.
- **CI** (`.github/workflows/ci.yml`): blocking `lint` (ruff `F,E9`), `typecheck` (mypy, zero
  errors), `test`, `ui-build` and `docker-build` (both images); advisory `standards` and
  `security-scan` (pip-audit). Job details: [guide 08 §8](../guides/08-operations-and-deployment.md).
- **Backup/restore** (`scripts/backup_restore.py`): tar.gz of the durable local state (Qdrant
  on-disk store, time-series SQLite, manifest, eval baseline/history, feedback log); restore
  refuses to overwrite without `--force`. A server-mode Qdrant uses its own snapshot API.
- **Dev hooks** (`.claude/hooks/`, registered in `.claude/settings.json`, both tracked): a
  PreToolUse guard (`guard_protected_paths.py`) vetoes file-tool access to `.env`, private keys
  and credential files (template variants such as `.env.example` pass) and any shell command
  naming `.env`; it fails closed, blocking a call it cannot parse. A PostToolUse ruff auto-fix
  runs on edited `.py`; the Stop hook runs the standards check (advisory) and a fast pytest
  subset (blocking), each only when relevant files changed since the last run.
  `test/unit/test_hooks.py` tests each hook both ways. Harness scores and the component
  register: [docs/harness/REAUDIT.md](../harness/REAUDIT.md).
