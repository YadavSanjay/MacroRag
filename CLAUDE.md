# MacroRAG — project instructions

Macro-economic RAG system. Ingests macro research (central banks, statistical
agencies, sell-side house views, energy & chip supply-chain sources) into a hybrid
vector index (prose) + a SQLite time-series store (exact numbers), then answers
questions with filtered hybrid retrieval → rerank → grounded, faithfulness-checked
generation. Its headline output is a **structured, citation-gated shock vector**
(19-field factor vocab) consumed by the downstream **faultline** stress engine. Usable
from CLI, REST API (FastAPI), and a React web UI (`ui/`).

Full narrative overview: [README.md](README.md). Design docs: [docs/design/](docs/design/).
Operator/user guides: [docs/guides/](docs/guides/).

## Documentation map — one owner per topic

Every topic has **one canonical home**; everywhere else links to it rather than restating
it. When you change behaviour, update the owner first, then check whether any pointer text
still reads true. Do not copy a table, a flag list, or an endpoint list into a second file.

| Topic | Canonical owner | Kind |
|---|---|---|
| What the system is / architecture at a glance | [README.md](README.md) | narrative |
| Repo map, conventions, agent rules | this file + module `CLAUDE.md` (`src/ingest`, `src/llm`, `ui`) | working notes |
| Install, secrets, external dependencies | [guide 01](docs/guides/01-setup-and-prerequisites.md) | operator |
| Ingest CLI, tiers, incremental download | [guide 02](docs/guides/02-data-ingestion.md) | operator |
| Index CLI, full vs incremental, manifest | [guide 03](docs/guides/03-indexing.md) | operator |
| Retrieval tuning · **LLM provider/tier config** · eval | [guide 04](docs/guides/04-query-retrieval-eval.md) | operator |
| **Every REST/MCP endpoint** · auth · jobs · **synthesis contract** · `world_view_id` definition | [guide 05](docs/guides/05-api-and-integration.md) | integrator |
| Running/developing the UI | [guide 06](docs/guides/06-ui.md) | operator |
| Plain-English product walkthrough | [guide 07](docs/guides/07-business-user-guide.md) | business |
| Runtime ops: compose/Helm, observability, caching, runbook, CI | [guide 08](docs/guides/08-operations-and-deployment.md) | operator |
| Image build → ECR → EC2 deploy | [guide 09](docs/guides/09-packaging-and-deployment.md) | operator |
| AWS resources infra must provision | [AWS deployment §4](docs/design/Macro_RAG_AWS_Deployment.md) | design |
| Shock-field names/units/signs | `src/synthesis/vocab.py` (code) → tabulated once in [guide 05 §5](docs/guides/05-api-and-integration.md) | contract |
| Service hardening (resilience, shared state, observability, governance, security) and the decisions that bound it | [Production Hardening](docs/design/Macro_RAG_Production_Hardening.md) (`§A`–`§L`) | design |
| Which sources and why · coverage of faultline's book · known gaps | [Source Catalog](docs/design/Macro_RAG_Source_Catalog.md) | design |
| External positioning / capability claims | [docs/Macro_RAG_Positioning.md](docs/Macro_RAG_Positioning.md) | brief |
| Agent-harness scores, component register, re-audit dates | [docs/harness/REAUDIT.md](docs/harness/REAUDIT.md) | harness |

Design docs under `docs/design/` describe the **current** design and the reasoning behind it
— not the history of how it was reached. When you change behaviour, update the affected
design doc in the same change; keep an earlier decision only when it still constrains the
code (e.g. the live-only `world_view_id`). If code and a doc disagree, the code wins and the
doc is fixed. Bare `§A`–`§L` references in code and docs point at
[Production Hardening](docs/design/Macro_RAG_Production_Hardening.md); "Ingestion Design §N"
points at [Ingestion Design](docs/design/Macro_RAG_Ingestion_Design.md).

## Environment

- **Always use the `py312` conda environment** for Python commands (running the
  app, tests, scripts). Invoke via `conda run -n py312 <cmd>` (e.g.
  `conda run -n py312 python -m pytest test/test_api.py -q`). Do not use the bare
  `python` on PATH — it lacks the project dependencies (fastapi, etc.).
- The `ui/` project needs a pinned Node/npm toolchain (node 24.14.0 / npm 11) on `PATH`;
  its install path is host-local, so keep it in your own environment rather than here.
  See `ui/CLAUDE.md` for the UI commands.
- `src/` is the import root — packages are imported as `pipelines`, `ingest`,
  `retriever`, … (not `src.*`). Running `python -m pipelines.cli …` therefore requires
  **`src` on `PYTHONPATH`** (e.g. PowerShell `$env:PYTHONPATH="src"`) or `cd src` first;
  it does **not** import from the bare repo root. (Tests handle this themselves via
  `test/conftest.py`, which inserts `src` on `sys.path`.)
- Secrets/API keys live in `.env` (gitignored): `OPENAI_API_KEY`, plus data-source
  keys (`FRED_API_KEY`, `BEA_API_KEY`, `BLS_API_KEY`, `EIA_API_KEY`, `CENSUS_API_KEY`,
  `FINRA_*`, `IEA_*`, `USITC_*`, `HUGGINGFACEHUB_API_TOKEN`, `HF_HOME`). Any secret may
  ALSO be supplied via a `<NAME>_FILE` variable (e.g. `OPENAI_API_KEY_FILE`) pointing at a
  file — for Docker/K8s secret mounts — resolved by `config.read_secret` (RAG layer) and
  `ingest.common.config.get_secret` (ingest layer), with the file winning over the plain
  env var (§L).
- **Deploy mode (`deploy_mode: local | aws`, default `local`).** One switch decides where the
  corpus **object data** (raw / processed / manifest) lives: the local filesystem, or **S3** in
  `aws` mode — behind the `utils.objectstore.ObjectStore` seam (`Local`/`S3`/`InMemory`),
  byte-identical in local mode. SQLite (time-series + ingest cache), traces, and eval artifacts
  stay on the local/EBS filesystem regardless; Qdrant + RagInference are reached by URL. Env:
  `MACRORAG_DEPLOY_MODE`, `MACRORAG_S3_BUCKET`/`_PREFIX`/`_REGION` (S3 creds come from the ambient
  AWS chain / EC2 instance role, never config; `boto3` is lazy-imported so local never needs it).
  Design + provisioning list: [docs/design/Macro_RAG_AWS_Deployment.md](docs/design/Macro_RAG_AWS_Deployment.md).
- **Shared inference service (default on).** **Both** retrieval **and indexing** embed via
  the **RagInference** service (one shared bge-m3 embedder + bge-reranker copy, shared with
  XbrlRag) instead of loading their own multi-GB models — factored out for constrained-RAM/CPU
  desktops. Both flows go through `embeddings.select_embedder`, so the query and corpus
  vectors always come from the same model. Default URL is `configs/rag.yaml` →
  `inference.remote_url` (`http://localhost:8799`); the service must be running for
  retrieval (`/query`, `/synthesize`, `eval`) **and** for `index`. Override per-process with
  the `RAGINFER_URL` env var (a different base URL, or empty to force in-process models); if
  the service is unreachable, `inference.fallback_local: true` loads the models locally
  rather than failing (forced **off** in the no-torch prod image so a stopped service fails
  loud). Qdrant (`:6333`) and OpenAI are separate.

## Commands

```bash
# Prereq for the module CLIs below: src must be on PYTHONPATH.
#   PowerShell:  $env:PYTHONPATH="src"     (or run from within src/)

# Pipeline CLI  (python -m pipelines.cli <cmd>)
python -m pipelines.cli index [--append]          # full rebuild (re-embed whole corpus)
python -m pipelines.cli index --incremental [--dry-run]   # embed only new+changed, delete departed
python -m pipelines.cli query "<question>" [--no-llm]
python -m pipelines.cli synthesize "<question>" [--horizon-days N] [--scenario "..."]  # gated shock vector
python -m pipelines.cli eval [--k 8] [--golden <path>] [--shock [<ref>]] [--judge] [--fail-on-regression] [--tol 0.05]   # retrieval eval (or shock-accuracy backtest); --judge runs the full pipeline + LLM judge (aggregate faithfulness, §B); --fail-on-regression is the CI drift gate (§K)
python -m pipelines.cli eval-matrix --task {answer,synthesize} [--models ...] [--variants ...] [--save-baseline] [--fail-on-regression]  # prompt × model regression matrix
python -m pipelines.cli serve [--host 127.0.0.1] [--port 8000]   # REST API
python -m pipelines.cli mcp [--transport stdio] [--principal P]  # MCP server

# Run golden-set questions end-to-end against live Qdrant + LLM (prose or shock vector)
python scripts/run_questions_live.py --file configs/eval/golden_user_facing.yaml --mode answer --limit 2
python scripts/run_questions_live.py --file configs/eval/golden_stress_backend.yaml --mode synthesize --limit 2 --horizon-days 180

# Ingestion  (python -m ingest ...)
python -m ingest --tier tier1_fed [--endpoints ...] [--limit N] [--refresh] [--list]

# MCP server (alt entrypoint)
python -m mcp_server                              # stdio

# Tests — all fakes (no torch, no network); `--collect-only` prints the current count
python -m pytest test/ -q

# Type check — config in mypy.ini (see Conventions); must stay at zero errors
mypy src/

# UI (from repo root; put the pinned node 24.14.0 toolchain on PATH first)
npm install --prefix ui
npm run dev --prefix ui                           # http://localhost:5173, proxies /api -> :8000
```

Scripts in `scripts/`: `normalize_vocab.py`, `rechunk_processed.py`, `reextract_dfast.py`,
`reextract_sep.py` (corpus maintenance — also exposed as `POST /admin/curate` jobs);
`retag_endpoint.py` (re-apply config tags to stored chunks; follow with a full `index`);
`backup_restore.py` (tar.gz backup/restore of the vector + time-series stores, sqlite, and
manifest, §L); `run_questions_live.py` (end-to-end golden-set runner, above);
`build_push_images.py` (build → in-image acceptance gate → ECR push for both images — see
[guide 09 §5.5](docs/guides/09-packaging-and-deployment.md)); `sync_corpus.py` (seed the S3
processed corpus from the local one, dry run unless `--apply`, never deletes — see
[guide 09 §6.6](docs/guides/09-packaging-and-deployment.md)); `harness/check_reaudit.py` (reports
when the harness re-audit in `docs/harness/REAUDIT.md` is due; `--gate` exits 1 once overdue).

**Two eval surfaces have no CLI — they are admin-API only:** `POST /admin/selfcheck`
(deterministic self-validation over the time-series store) and `POST /admin/backtest` +
`GET /admin/backtest/episodes` (realized-episode backtest). The CLI covers `eval`,
`eval --shock` and `eval-matrix` only. Full endpoint reference:
[guide 05](docs/guides/05-api-and-integration.md).

## Layout

```
src/
  ingest/        tiered source handlers → ParsedDocument JSON (narrative chunks + numeric series)
    common/      shared handler infra: base, handler_base, chunking, dateextract, pdf/html_extract,
                 sdmx, spreadsheet, http_client, cache, storage, models, config, browser
    <tier>/      one package per source family: fed, treasury, usstats, intl, house,
                 power, chips, aimacro, scenarios — each has ingestor.py, common_base.py,
                 parsers.py, and handlers/<source>.py
    __main__.py, cli.py
  embeddings/    base + bge_m3 (BAAI/bge-m3, dense 1024-d + sparse) + hashing (test fake) + remote
  vectorstores/  base, qdrant_store, memory (test fake), timeseries_store (SQLite), filters
  retriever/     analysis (route/paraphrase/decompose/HyDE), retriever, fusion (RRF), rerank,
                 ranking (time-decay), mmr, multihop, remote_reranker
  agent/         typed plan-execute agent: planner, executor, calculator (safe quant), agent, schema
  synthesis/     shock-vector synthesizer, gate (citation gate), validate (plausibility band /
                 series anchor / cross-field coherence), vocab, schema, diagnostics
  factors/       factor discovery: discoverer, gate, vocab, schema
  llm/           base (LLMClient + schema-validated complete_json) + openai_client + anthropic_client
                 + fake; registry (versioned prompt registry) + prompts (thin shim over it);
                 generate, verify, understanding, escalation (quality-signal model escalation),
                 resilience (retry/backoff/circuit-breaker), schema_health (structured-output
                 outcome tracking), sanitize (prompt-injection defense), pricing, usage
  pipelines/     cli, index_pipeline, query_pipeline, hydrate (time-series hydration)
  api/           FastAPI: app, server, models, admin, corpus, discovery, jobs, jobstore
  mcp_server/    MCP surface mirroring the REST endpoints (server.py, __main__.py)
  eval/          harness, metrics (hit@k/recall@k/MRR), judge, drift, matrix (prompt×model
                 regression), shock_eval, shock_metrics, feedback_reconcile,
                 selfcheck (self-validation: known-answer cases generated from the
                 time-series store + negative controls; deterministic, no judge),
                 episodes (realized-episode backtest over the ingested `historic`
                 series — `grounding_fidelity` vs `ex_ante_skill`, never merged)
  utils/         config (RagSettings), objectstore (local/S3 corpus storage seam), corpus, manifest,
                 trace, observability, logging, text, schema, cache, metrics, alerting, costguard,
                 ratelimit, locks, redisconn, fakeredis
prompts/         versioned prompt registry (repo root): system/<role>.md +
                 tasks/<task>/{base.md, base.meta.yaml, schema.json} — resolved by src/llm/registry.py
configs/         see below
docs/            design/ (source catalog, ingestion, pipeline, LLM harness, production
                 hardening, UI, UI containerization, AWS deployment) + guides/ (01–09 +
                 README) + images/ (README architecture diagram) + Macro_RAG_Positioning.md
                 (positioning/capability brief) + harness/REAUDIT.md (agent-harness re-audit)
scripts/         maintenance jobs + live question runner + image build/push
test/            unit + API/admin/corpus/mcp integration tests (fakes only); spec/<id>/ = locked spec tests
data/            (gitignored) raw/ processed/ cache/ vectorstore/qdrant/ traces/
infra/           two images — lightweight backend (Dockerfile, no torch) + UI (ui/Dockerfile,
                 nginx SPA + /api proxy) — combined via compose (UI/API/Qdrant/Redis/OTel);
                 embedder/reranker come from external RagInference (RAGINFER_URL). Helm, OTel config.
                 config/rag.aws.yaml + docker/docker-compose.aws.yml = single-EC2 aws deploy_mode stack
.github/         workflows/ci.yml — CI (ruff lint + standards + mypy + tests + ui-build + pip-audit +
                 docker-build, §L); tdd-spec.yml + tdd-path-guard.yml — the test-first gates
.claude/         tracked: hooks/ + settings.json (dev hooks, see Conventions), rules/, review/ (the
                 review-core lens pack + code map), tdd/ (spec locks + profile). Local only
                 (gitignored): settings.local.json, state/, tdd/state/, review/out/
tools/           standards/check_standards.py (lint ratchet + docstring checks), tdd/ (test-first tooling)
docs/specs/      one spec per test-first change (`<id>.md`), hash-locked with its tests
```

## Config

- `configs/rag.yaml` — the main runtime tuning file (**committed with tuned overrides**,
  not just code defaults). Key knobs (all documented inline with rationale — read the
  comments before changing):
  - `llm`: `provider` + a per-provider `profiles:` block giving the model tiers — `plan`
    (planner/router/verify — **must be capable**, drives retrieval quality), `answer`
    (cheap simple-query gen), `complex` (complex routes + synthesis + factor discovery),
    optional `default`/`escalation`. Switching `provider` remaps every tier at once; a flat
    `plan_model`/`answer_model`/`complex_model` key still overrides a single tier.
    `complex_routes` lists the routes that use `complex`. (Prompts themselves are versioned
    separately under `prompts/` via the registry — see Layout.)
  - `escalation`: quality-signal model escalation (retry a deteriorated cheap-model pass on
    a higher tier, keep-if-better; upgrades logged to the trace `escalations[]`) — **off by
    default**.
  - `retrieval`: `rerank_candidates` (CPU rerank is the latency bottleneck),
    `min_rerank_score` (relevance floor / refusal gate), `multi_hop_max_hops`
    (iterative causal decomposition; off by default, CPU-costly).
  - `synthesis`: `min_citation_chunks` (shock-field citation gate),
    `enable_analog` (analog/proxy grounding), `drop_invalid` + `horizon_check` /
    `direction_check` (hard validation failures are dropped, not just flagged),
    `scenario_study_slots` (winner slots reserved for quantified scenario studies and
    episode cards), `rerank_candidates` (smaller synthesis-only rerank pool for latency).
  - `redis.url` (+ env `MACRORAG_REDIS_URL`, §I) backs the per-feature
    `backend: memory|redis` toggles — `jobs`, `cost`, `auth.rate_limit_backend`,
    `semantic_cache.backend` — each independently memory (default) or redis-backed.
  - `llm.resilience.*` — provider-call retry/backoff + circuit breaker (§H).
  - `alerting.*` / `eval.*` / `cost.*` — drift-alert routing, eval thresholds, and cost
    governance (§K).
  - `api.max_concurrency` (in-flight request cap → 503 on saturation) + `api.cors_allow_origins`
    (§L); `observability.metrics_enabled` (Prometheus metrics, §J).
  - `deploy_mode` (`local|aws`) + `storage.*` (`backend`, `s3_bucket`, `s3_prefix`, `s3_region`) —
    where the corpus object data lives (local FS vs S3); env `MACRORAG_DEPLOY_MODE` /
    `MACRORAG_S3_*`. `qdrant.api_key_env`/`https`/`timeout` secure a managed/in-VPC Qdrant server.
    See `infra/config/rag.aws.yaml` + the AWS deployment design.
- `configs/ingest/` — one `tier*.yaml` per source family + `common.yaml` + `vocabulary.yaml`
  (factor/shock vocab). Tiers: tier1_fed, tier2_treasury, tier3_usstats, tier4_intl,
  tier5_house, tier6a_power, tier6b_chips, tier6c_aimacro, tier7_scenarios.
- `configs/eval/` — the two golden sets actually used are `golden_user_facing.yaml`
  (prose) and `golden_stress_backend.yaml` (shock vectors); `paths.golden_candidates` points
  at the feedback→golden review file (positive, chunk-citing feedback becomes candidate golden
  entries for a human to promote, §B).
  `configs/llm_pricing.yaml`, `configs/logging.yaml`.

## Conventions

- **Everything external sits behind an interface**, so the whole pipeline runs under
  test with fakes — hashing embedder, in-memory vector store, identity reranker,
  scripted LLM. **No torch, no network in tests.** When adding a component, define/extend
  the `base.py` interface and provide a fake for tests.
- **Ingest handlers follow a fixed shape**: each source family is a package under
  `src/ingest/<tier>/` with an `ingestor.py`, a `common_base.py`, `parsers.py`, and
  one handler per source in `handlers/`. Shared parsing/fetch/dating utilities live in
  `src/ingest/common/`. Dates for narrative sources are derived via
  `src/ingest/common/dateextract.py` (all narrative handlers are dated corpus-wide).
- Indexing supports two paths: a **full re-embed** of the whole corpus on CPU bge-m3
  (~80 min; `index`, or `index --append` which keeps the collection but still re-embeds
  everything), and a **true incremental refresh** (`index --incremental`) that diffs each
  processed chunk's `content_hash` against the store and embeds only new+changed chunks,
  deleting departed ones (`--dry-run` previews the plan). Prefer `--incremental` after an
  incremental ingest; use a full rebuild after an embedder/chunking change.
- Each index stamps a `world_view_id` (corpus-state id); shock vectors carry the
  `world_view_id` they were synthesized under. It is **content-addressed** —
  `macro-<YYYY-MM-DD>-<12-hex digest>` over the sorted `(chunk_id, content_hash)` pairs
  (`utils.manifest.corpus_digest`), so an unchanged corpus re-indexed keeps its id and two
  different same-day corpora cannot collide. Manifests written before digests existed keep
  the legacy date-only form rather than being given a false identity retroactively.
  Every build is appended to a **manifest history log** (`manifest_history.jsonl`, beside
  the manifest in the same object store) — `GET /admin/manifest/history` lists builds with
  a field-level diff against the previous one, and `GET /admin/manifest/verify?world_view=…`
  answers "was this ever our corpus, and is it still live?" for an old shock vector.
  This is an audit **trail, not a snapshot store**: re-indexing still replaces the live
  corpus, so you still cannot retrieve or re-synthesize against a superseded
  `world_view_id` (§F: the live-only guarantee is deliberate — faultline does not replay
  against superseded corpora).
- **Shock-accuracy evidence comes in two kinds and they must never be averaged.**
  `eval/shock_eval.py` scores against *projected* official paths (the DFAST/EBA
  baseline→adverse delta) — that is **transcription fidelity**, not forecast skill.
  `eval/episodes.py` scores against *realized* moves derived from the ingested `historic`
  series (1976-Q1..2025-Q4, 28 variables) in two separately-named modes:
  `grounding_fidelity` (evidence visible) and `ex_ante_skill` (retrieval bounded to
  `release_date` < episode start). Summary keys are mode-prefixed so they cannot merge.
  A high `ex_ante_skill_abstention_rate` is a **corpus-depth** statement (most docs are
  recent), not a quality failure — report it alongside accuracy, never folded in.
- **`principal` ≠ `tenant_id` — two different things, deliberately.** A **principal** is the
  caller identity behind an API key (`auth.api_keys` maps key → principal): auth, rate limit,
  cost attribution. It is **not** a data-isolation boundary. `MetaFilter.tenant_id` is a
  separate **dormant** hook that isolation *would* use — nothing writes it at ingest, so it
  partitions nothing today; the API stamps the principal there only so retrieval would
  discriminate correctly if a private per-tenant ingest path were ever added (§A).
  Never describe the API as multi-tenant: the corpus is shared. Deprecated aliases kept for
  compatibility: config `cost.per_tenant_daily_usd` (→ `per_principal_daily_usd`) and the
  `--tenant` CLI/MCP flag (→ `--principal`).
- **Type checking is a gate, not a suggestion** — `mypy src/` is **zero errors** and runs in
  CI. Config: [mypy.ini](mypy.ini) (pydantic plugin on; `mypy_path = src` because `src` is the
  import root). Run bare it reported ~77 issues that were almost all third-party shape noise,
  which teaches everyone to ignore it; the config silences that noise **per module and per
  error code** so a non-zero exit means something is genuinely wrong. Two rules when editing:
  suppressions are never global (a blanket `ignore_errors` on our own code hides real defects),
  and every override states *why* — if a library ships stubs or a lazy import becomes a hard
  dependency, delete the override. Prefer an inline `# type: ignore[code]  # reason` over
  widening a module rule. Not enabled yet (deliberately): `disallow_untyped_defs`,
  `check_untyped_defs` — tighten module-by-module rather than all at once.
- **API guardrails** (§L): a job-concurrency lock returns **409** on a conflicting in-flight
  job, the cost cap returns **402** when a request would exceed budget, input-bound violations
  return **422**, and `api.max_concurrency` saturation returns **503**.
- **Coding standards:** Google Python Style Guide with house deviations, for new and touched code
  ([.claude/rules/python.md](.claude/rules/python.md)); RAG rules in [.claude/rules/rag.md](.claude/rules/rag.md). Lint is
  ruff only (config [ruff.toml](ruff.toml), no formatter). Existing findings live in
  `ruff-burndown.toml`, which may only shrink — never add entries, ignores or overrides to get
  green; `python tools/standards/check_standards.py ratchet` checks it. CI's `standards` job
  reports the full tier (advisory); the blocking `lint` job still checks `F,E9` only.
- **Dev hooks** (`.claude/hooks/` + `.claude/settings.json`, §L): a fail-closed PreToolUse
  secrets guard vetoes `.env` and key-file access, a PostToolUse ruff auto-fix runs on edited `.py`, and the Stop hook runs
  the standards check (advisory) and the fast-pytest gate — both only when relevant files changed
  since the last check. The hooks, their registration and `.claude/rules/` are **tracked**;
  personal permissions go in `.claude/settings.local.json`, and `.claude/state/` (hook run state)
  stays gitignored. `.claude/review/` is the review-core lens pack (profile, lenses, suppressions,
  `codemap.json`); its `out/` reports stay local.
