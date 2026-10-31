# 08 — Operations & Deployment

Running MacroRAG beyond a single dev process: Docker Compose, Kubernetes (Helm),
observability, caching, a routine operations runbook, and the operational failure
modes that are characteristic of RAG systems.

> Audience: operators / SRE. Infra files live under `infra/`
> ([infra/README.md](../../infra/README.md)). **Building and publishing the images** is
> [09 — Packaging & Deployment](09-packaging-and-deployment.md); this guide is the runtime.

---

## 1. Deployment topology

The compose stack is five services, with the **UI as the front door**:

| Service | Image | Ports | Role |
|---|---|---|---|
| **ui** | `macrorag-ui` (built from `ui/Dockerfile`) | 8080 | nginx: serves the SPA **and** reverse-proxies `/api/*` to `app` (stripping `/api`) — the only service that needs to be exposed |
| **app** | `macrorag` (built from `infra/docker/Dockerfile`) | 8000 | FastAPI / uvicorn API |
| **qdrant** | `qdrant/qdrant:v1.18.2` | 6333 (HTTP), 6334 (gRPC) | Vector store (server mode) |
| **redis** | `redis:7-alpine` | 6379 | Optional shared state (caches, rate limit, jobs, cost counters) |
| **otel-collector** | `otel/opentelemetry-collector-contrib:0.116.1` | 4317 (gRPC), 4318 (HTTP) | Trace collection |

Every published port binds to `127.0.0.1`, because auth is off by default
([guide 05 §2](05-api-and-integration.md#2-authentication)). Turn auth on before changing a binding.

Plus one service that is **deliberately not in this compose**:

> **RagInference** (the sister project) hosts the single shared bge-m3 embedder +
> bge-reranker. The backend image ships **no torch**, so both indexing and retrieval reach
> it over HTTP via `RAGINFER_URL` (compose default `http://host.docker.internal:8799`).
> **Start it before `docker compose up`.** `inference.fallback_local` is forced *off* in
> `rag.prod.yaml` — with no torch in the image, an unreachable service must fail loudly
> rather than attempt an impossible in-process load. See
> [01 §4.5](01-setup-and-prerequisites.md).

> **Use a Qdrant *server* in production.** The embedded on-disk mode is single-writer
> and can deadlock on bulk upsert. Point the app at `qdrant.url` (see below).

**Dual-mode deploy (`deploy_mode: local | aws`).** The same code runs two ways, chosen by
one switch (§2.1). In `aws` mode the corpus **object data** (raw / processed / manifest) moves
to **S3**, Qdrant and RagInference run on their own in-VPC hosts, and the SQLite time-series +
traces stay on an **EBS** volume. Full design + change list:
[Macro_RAG_AWS_Deployment.md](../design/Macro_RAG_AWS_Deployment.md).

---

## 2. Docker Compose (local / single-host)

```bash
# start RagInference (sister project) first — the backend has no torch

# from the repo root — build + start the stack; front door is the UI on :8080
docker compose -f infra/docker/docker-compose.yml up --build

# just the dependencies (run the API/CLI on the host against them)
docker compose -f infra/docker/docker-compose.yml up -d qdrant redis otel-collector

# build the index inside the running app container (embeds via RagInference)
docker compose -f infra/docker/docker-compose.yml exec app python -m pipelines.cli index

# smoke a query — directly, or through the UI's /api proxy
curl -s localhost:8000/query -H 'content-type: application/json' \
  -d '{"question":"DFAST 2026 severely adverse unemployment path?"}' | jq
curl -s localhost:8080/api/healthz | jq
```

Key mounts & env (from `docker-compose.yml`):

- `../config/rag.prod.yaml → /app/configs/rag.yaml:ro` — production config overrides.
- `../../data → /app/data` — processed corpus + index artifacts (persist these).
- `hf_models → /models` with `HF_HOME=/models` — cached embedder/reranker weights.
- `env_file: ../../.env` — secrets (`OPENAI_API_KEY`, etc.).
- Healthcheck: `GET /healthz` every 30s.

The image sets `PYTHONPATH=/app/src`, so module imports work without extra config.

### Production `rag.prod.yaml` essentials

```yaml
qdrant:
  url: http://qdrant:6333     # server mode (NOT embedded on-disk)
  path: null
cache:
  enabled: true
  backend: redis
  redis_url: redis://redis:6379/0
observability:
  otel_enabled: true
  otel_endpoint: http://otel-collector:4317
auth:
  enabled: true               # turn on keys + rate limits in shared envs
```

### 2.1 AWS single-EC2 (`deploy_mode: aws`)

> Building the images, pushing them to **AWS ECR**, and the one-time EC2 bootstrap are covered
> step-by-step in [09 — Packaging & Deployment](09-packaging-and-deployment.md). This section
> covers the runtime shape.

The AWS target runs the service tier as **Docker Compose on one EC2**, with Qdrant and
RagInference on their own in-VPC hosts and the corpus object data on S3. Use the standalone
[`docker-compose.aws.yml`](../../infra/docker/docker-compose.aws.yml) +
[`rag.aws.yaml`](../../infra/config/rag.aws.yaml) (assumes the VPC / EBS / S3 bucket / IAM role
already exist — provisioned by the separate infra project):

```bash
# on the instance (EBS mounted at /mnt/macrorag-data, .env rendered from Secrets Manager)
export MACRORAG_S3_BUCKET=my-corpus-bucket
export MACRORAG_QDRANT_URL=https://qdrant.internal:6333   # TLS, self-signed (rag.aws.yaml: verify false)
export RAGINFER_URL=http://raginference.internal:8799
docker compose -f infra/docker/docker-compose.aws.yml up -d
# on-demand ingest / index (CLI or the UI admin surface) — writes raw/processed to S3
docker compose -f infra/docker/docker-compose.aws.yml exec app python -m pipelines.cli index
```

**What goes where** (see the design doc's data-placement matrix):

| Artifact | `local` | `aws` |
|---|---|---|
| raw / processed / manifest | local `data/` | **S3** (`ObjectStore` seam) |
| timeseries.sqlite, ingest cache, traces, eval | local `data/` | **EBS** (`/app/data`) |
| Qdrant index | the compose `qdrant` container (named volume) | **separate host** (`qdrant.url`) |
| model weights | local `HF_HOME` | **RagInference host only** |

**The switch + env vars** (precedence env > `rag.yaml` > code defaults):

| Env var | Effect |
|---|---|
| `MACRORAG_DEPLOY_MODE` | `local` (default) or `aws` — `aws` makes `storage.backend` default to `s3` |
| `MACRORAG_S3_BUCKET` / `MACRORAG_S3_PREFIX` / `MACRORAG_S3_REGION` | corpus bucket + layout (`{prefix}/raw/ingest`, `/processed/ingest`, `/vectorstore/manifest.json`) |
| `MACRORAG_QDRANT_URL` | external Qdrant server (server mode required) |
| `RAGINFER_URL` | external RagInference (embedder + reranker; the image ships no torch) |

Notes:
- **S3 credentials** come from the EC2 **instance IAM role** (never in config/env) — boto3 picks
  them up automatically. `boto3` is lazy-imported by `S3ObjectStore`, so `local` mode never needs it.
- **Secrets** (`OPENAI_API_KEY`, source keys, an optional Qdrant `api_key_env`) resolve via the
  `<NAME>_FILE` mount convention or plain env — render `.env` from Secrets Manager at boot.
- **Jobs stay in-process** — a single EC2 is a single writer, so the in-process exclusive lock is
  sufficient; no Redis job backend is required (Redis is still used for the L1/L2 cache).
- The two SQLite stores are **derived/rebuildable** (`timeseries.sqlite` from the S3 processed
  corpus, the ingest cache is disposable), so EBS snapshots are enough — no RDS.

---

## 3. Kubernetes (Helm)

```bash
# create the secret the chart references
kubectl create secret generic macro-rag-secrets \
  --from-literal=OPENAI_API_KEY=sk-...

# install — raginferUrl is REQUIRED (the API image ships no models)
helm install macro-rag infra/helm -f infra/helm/values.yaml \
  --set raginferUrl=http://raginference.infer.svc:8799 \
  --set ingress.enabled=true --set ingress.host=macro-rag.example.com
```

The chart renders **two workloads** — the API (`:8000`) and the UI (`:8080`), each with its
own Deployment + Service. `values.yaml` covers images, `replicaCount`, resource
requests/limits, the dependency URLs (`qdrant.url`, `redis.url`, `otel.endpoint`) and the
`secretName`. Notes:

- **`raginferUrl` is required.** The API image is model-free, so embedding and reranking
  come from the external RagInference service. Put the GPU/memory budget *there* (the bge
  models are ~1.4 GB resident); the API pods stay small and stateless and need no GPU.
- `ui.*` sizes the UI (tiny, ~64–128Mi); its `API_UPSTREAM` is wired to the API Service
  automatically, and the optional Ingress fronts **only** the UI — the API is never exposed
  directly because `/api` is proxied inside the pod.
- `configOverride` mounts a **complete** `rag.yaml` over the image's baked config; its main
  use is `inference.fallback_local: false`, which cannot be set by env.
- Deploy Qdrant / Redis / OTel separately (operators or subcharts) and wire their URLs.
- Scale the API horizontally; the exclusive job lock enforces the single-writer rule when
  (re)building the index — a concurrent index/ingest/curate returns **409**
  ([Production Hardening §H](../design/Macro_RAG_Production_Hardening.md)). Set `api.max_concurrency` to
  cap in-flight requests (excess → **503**), protecting the rerank bottleneck
  ([Production Hardening §L](../design/Macro_RAG_Production_Hardening.md)). With `replicaCount > 1`, set
  the Redis-backed backends (§5) so the lock, rate limit and cost counters are shared.

---

## 4. Observability

- **Per-query traces** are always written to `data/traces/queries.jsonl` (route,
  candidate scores, timings) regardless of OTel — inspect via `GET /admin/traces` or
  the UI **Traces** screen.
- **OpenTelemetry** (optional): set `observability.otel_enabled: true` and
  `otel_endpoint`. The collector config is `infra/otel/otel-collector-config.yaml`
  (OTLP in on 4317/4318; exports to `otlp/tempo` and the `debug` exporter —
  [Production Hardening §J](../design/Macro_RAG_Production_Hardening.md)). Spans
  cover query, retrieve, generate, verify.
- **Prometheus metrics**: `GET /metrics` (text exposition, gated by
  `observability.metrics_enabled`, default on) — scraped per replica. Includes the real
  request-latency histogram `macrorag_http_request_duration_seconds` plus LLM spend/token
  counters ([Production Hardening §J](../design/Macro_RAG_Production_Hardening.md)).
- **JSON logs**: set `MACRORAG_LOG_JSON=1` for structured (machine-parseable) log output.
  The file log (`logs/macro_app.log`) is relative to the repo root, or to
  `MACRORAG_LOG_DIR` when set.
- **Feedback** lands in `data/traces/feedback.jsonl` (`GET /admin/feedback`).
- **Health & staleness**: `GET /healthz` (liveness + index built) and
  `GET /admin/manifest` (snapshot, staleness, `world_view_id`). The UI **Health**
  screen surfaces alerts.

---

## 5. Caching

| Layer | Setting | Notes |
|---|---|---|
| L1 exact-match | `cache.enabled`, `cache.ttl_seconds`, `cache.max_entries` | In-process by default; set `cache.backend: redis` + `redis_url` for shared cache |
| L2 semantic | `semantic_cache.enabled`, `semantic_cache.threshold` (≈0.97) | Reuses answers for near-identical query embeddings; off by default |

Both layers are **scoped** (spec `cache-scope`): an entry is reused only for the same
`filters`, the same cache version (answer prompt, models, retrieval options and route), and the
same corpus `world_view_id`, which is read per request. So a re-index invalidates cached
answers at once, with no restart or flush, and a near-identical question under different
filters, or a synthesize call after a plain answer, never reuses another request's chunks.
Neither layer stores a refusal, so a transient one recovers on the next request, and a request
with `no_cache: true` neither reads nor writes them
([guide 05](05-api-and-integration.md)).

### Shared state (Redis)

For multi-replica deployments, Redis backs the shared state a single-process cache can't.
Point everything at one URL — `redis.url` (or the `MACRORAG_REDIS_URL` env var) — then
opt each feature in via its per-feature backend: `auth.rate_limit_backend` (rate-limit
buckets), `jobs.backend` (job store + exclusive lock + idempotency), `semantic_cache.backend`
(L2 semantic cache), and `cost.backend` (daily cost counters). **Every one falls back to
in-process** when Redis is absent or its backend is left unset, so a single-process desktop
run needs no Redis ([Production Hardening §I](../design/Macro_RAG_Production_Hardening.md)).

### LLM resilience, cost caps & alerting

- **LLM resilience** (`llm.resilience.*`): transient 429/5xx/timeout failures are retried
  with backoff + jitter behind a circuit breaker, with explicit per-call timeouts — a
  flaky provider degrades gracefully instead of erroring
  ([Production Hardening §H](../design/Macro_RAG_Production_Hardening.md)).
- **Hard cost caps** (`cost.*`): a daily LLM spend ceiling; once hit, generation is
  admission-controlled and the API returns **402** until the daily reset
  ([Production Hardening §K](../design/Macro_RAG_Production_Hardening.md)).
- **Alerting** (`alerting.*`): a Slack webhook fires on cost-cap, eval-regression and
  corpus-staleness events ([Production Hardening §K](../design/Macro_RAG_Production_Hardening.md)).

---

## 6. Routine operations runbook

**Refresh the corpus (incremental, the common case):**
```bash
python -m ingest --tier tier1_fed --refresh      # repeat per tier you track
python -m pipelines.cli index --incremental --dry-run   # preview what will change
python -m pipelines.cli index --incremental      # embed only new+changed, delete departed
python -m pipelines.cli eval                      # confirm no retrieval regression
```
or via the job API: `POST /admin/ingest` → `POST /admin/index {"append":true}` →
`POST /admin/eval`, watching `/admin/jobs/{id}/stream`. (The `/admin/index` job runs a
full/append rebuild; the CLI `--incremental` path is the leanest option for a desktop.)

**Full rebuild (after model/chunking/vocab changes):**
```bash
python scripts/normalize_vocab.py          # if vocabulary.yaml changed
python -m pipelines.cli index              # full recreate
python -m pipelines.cli eval --k 8
```

**Promote a new quality baseline (after a verified improvement):**
`POST /admin/eval/baseline` with the new summary (or manage `data/eval/baseline.json`).

**Periodic deeper quality checks (admin API only — no CLI):**
`POST /admin/selfcheck` runs the judge-free self-validation harness against the time-series
store, and `POST /admin/backtest` scores shock vectors against realized episode moves. Both
return jobs; watch them via `/admin/jobs/{id}/stream`. What they measure — and why the
`grounding_fidelity` and `ex_ante_skill` numbers must never be averaged together, nor with
the `eval --shock` transcription-fidelity number — is in
[04 §5](04-query-retrieval-eval.md).

**Confirm an index build actually changed the corpus:** `GET /admin/manifest/history` shows
each build with a diff against the previous one; an unchanged corpus keeps the **same**
content-addressed `world_view_id` ([03 §4](03-indexing.md)).

**Backup / state to persist:** `data/processed/ingest/` (source of truth for the
index), `data/vectorstore/` (Qdrant + time-series + manifest + `manifest_history.jsonl`),
`data/traces/` (traces + feedback), `data/cache/ingest_cache.sqlite` (so incremental ingest
stays incremental), and your `configs/`. `scripts/backup_restore.py backup <archive.tar.gz>`
bundles the durable state (local Qdrant store, time-series db, manifest, eval baseline,
feedback log) into a **tar.gz**, and `restore <archive> [--force]` restores it (`--force`
overwrites existing state) — [Production Hardening §L](../design/Macro_RAG_Production_Hardening.md).
Server-mode Qdrant is not dumped; use its own snapshot API.

---

## 7. Troubleshooting — operational RAG failure modes

These are the issues that recur with RAG systems in production. Each maps to a guide
section for the deeper fix.

| Symptom | Likely cause | Fix |
|---|---|---|
| **Stale answers** after a release | Index not refreshed, or cache serving old results | Incremental ingest + `index --incremental`; clear cache / rely on TTL ([02](02-data-ingestion.md), [03](03-indexing.md)) |
| **Answers cite outdated docs** over newer ones | Time-decay off | `ranking.time_decay: true` in `rag.yaml` ([04 §3](04-query-retrieval-eval.md)) |
| **Hallucinated numbers** | LLM guessing instead of using exact series | Keep `hydrate_timeseries` + `plan_execute` on; ensure the series is in the time-series store ([03](03-indexing.md)) |
| **Low recall** (relevant doc never retrieved) | Over-tight filters, or doc not indexed | Debug with `query … --no-llm`; confirm it's ingested+indexed; the retriever retries unfiltered when filters starve recall |
| **Over-refusal** ("not enough evidence") | `min_rerank_score` too high | Lower the floor; verify reranker is enabled ([04](04-query-retrieval-eval.md)) |
| **Qdrant deadlock / "already accessed"** | Embedded on-disk used under concurrency | Switch to Qdrant **server** (`qdrant.url`); one writer for embedded mode ([03 §3](03-indexing.md)) |
| **OOM / slow embeddings & rerank** | CPU-only, batch too large, no quantization | GPU node; lower `embedding.batch_size`; `qdrant.quantization: true` ([01 §4](01-setup-and-prerequisites.md)) |
| **LLM 429 / cost spikes** | No caching, frontier model over-used, rate limits | Transient 429/5xx retried automatically (`llm.resilience.*`); a daily `cost.*` cap admission-controls spend (→ `402`) with `alerting.*` warnings. Enable L1/L2 cache; tune tiered models ([04 §4](04-query-retrieval-eval.md); [Production Hardening §H](../design/Macro_RAG_Production_Hardening.md), [§K](../design/Macro_RAG_Production_Hardening.md)) |
| **Vector dimension mismatch** on upsert | Embedder changed against an old collection | Full rebuild so the collection matches the new vectors ([03](03-indexing.md)) |
| **Ingestion gets nothing new** | Discovery cache 304 / content already seen | `--refresh`, or delete `data/cache/ingest_cache.sqlite` for a full re-download ([02 §4](02-data-ingestion.md)) |
| **Index drift / eval regression** after a change | Config/model change hurt retrieval | Compare against baseline; revert or re-tune; re-run `eval` ([04 §5](04-query-retrieval-eval.md)) |
| **Shock-vector fields dropped** | Citation gate (no dated source) | Ingest supporting sources; check `chunks[].release_date` ([05 §5](05-api-and-integration.md)) |
| **Secrets exposure** | Live keys leaked (logs, a committed secrets file) | Rotate keys; keep the secrets file gitignored; use a secret manager ([01 §8](01-setup-and-prerequisites.md)) |

---

## 8. CI

GitHub Actions (`.github/workflows/ci.yml`) runs on Python 3.12 with **light, pinned
dependencies and fakes only** — no torch, no GPU, no network — so it's fast on every push
and pull request:

| Job | What it gates |
|---|---|
| `lint` | `ruff check src test --select F,E9` — **blocking**, critical bugs only (pyflakes + syntax) |
| `standards` | ruff at the configured tier (`ruff.toml`), Google docstrings on changed lines, and the `ruff-burndown.toml` ratchet (may only shrink) — **advisory**, reported in the job summary |
| `typecheck` | `mypy src/` against `mypy.ini` — kept at **zero errors**; suppressions are per-module + per-code and each states why |
| `test` | `pytest test/` on the light dependency set |
| `ui-build` | `npm run build` in `ui/` (runs `tsc`, so it is also the UI type gate) |
| `security-scan` | `pip-audit` — advisory, so a fresh CVE feed entry doesn't block a merge |
| `docker-build` | Builds **both** images (backend + UI). **No push** — publishing to ECR runs through `scripts/build_push_images.py` ([09 §5](09-packaging-and-deployment.md)) |

Two more workflows enforce the test-first flow: `tdd-spec.yml` (runs every spec test: graduated tests must pass, active-lock
tests must pass or fail for the right reason, and an `impl/<id>` PR must turn its lock green) and `tdd-path-guard.yml` (classifies each PR as spec, maintenance or
implementation by the files it touches).

([Production Hardening §L](../design/Macro_RAG_Production_Hardening.md).) Run the same gates locally:

```bash
pytest test/
mypy src/
ruff check src test --select F,E9
python tools/standards/check_standards.py ratchet
```

Back to the [Guides index](README.md).
