# MacroRAG — UI Design

The React web UI (`ui/`) over the REST API: who it serves, how it is organized, what each screen
does and which endpoints back it. `ui/src/` is the source of truth for exactly what exists.
Running and developing the UI: [guide 06](../guides/06-ui.md) and [ui/CLAUDE.md](../../ui/CLAUDE.md).
Packaging: [UI containerization](UI_Containerization.md). The endpoint reference:
[guide 05](../guides/05-api-and-integration.md).

---

## 1. Purpose and areas

MacroRAG is usable from the CLI, REST and MCP; the UI puts its business and operational surfaces
in one app with two areas:

- **Area A — Business.** **A1 end-user console** (ask cited questions, discover risk factors,
  build and export shock vectors, explore the corpus) and **A2 internal maintenance** (operators
  who keep the corpus, index and quality healthy).
- **Area B — System.** For engineers: the component map, end-to-end request traces, health and
  store inspectors.

Design goal: **elaborate but curated** — cover the important flows without drowning the user.

## 2. Personas and navigation

| Persona | Area | Primary jobs |
|---|---|---|
| Macro analyst / PM | A1 | Ask cited questions, discover ranked risk factors, build and export shock vectors |
| Research desk lead | A1 | Browse the corpus by facet, validate citations, give feedback |
| Corpus operator | A2 | Run ingestion tiers, curate vocabulary and doc types, rechunk/re-extract |
| Index / quality operator | A2 | Build the index, watch staleness, run evals and backtests, review drift and feedback |
| Platform engineer | B | Understand component wiring, trace requests stage by stage, watch health |

Left rail (`ui/src/app/nav.ts`):

```
Business              /ask · /factors · /scenario · /corpus
Internal maintenance  /ingestion · /curation · /index · /quality
System                /system/map · /system/traces · /system/health · /system/stores
```

## 3. Shell and shared primitives

- **Shell** (`app/`): nav rail, top bar (world-view-id chip from the manifest, theme toggle),
  content area, detail drawer.
- **Auth** (`ApiKeyGate`): the API key is typed in the browser, kept in `localStorage` and sent
  as `X-API-Key`. The backend maps it to a **principal** — identity, rate limit and cost
  attribution, **not** a data-isolation scope (the corpus is shared). Auth is enforced only when
  the backend sets `auth.enabled=true`; `/admin/*` additionally requires a key listed in
  `auth.admin_api_keys` (401 missing/invalid, 403 non-admin).
- **Filter editor** (`FacetFilterEditor`): one `FilterModel` (tiers, buckets, doc types,
  entity/factor tags, publishers, date range) shared by Ask, Factors, Scenario Studio and Corpus.
  Vocabulary-aware: aliases canonicalize; unknown tokens are kept and marked non-canonical.
- **Citations** (`Citations`, `EvidenceRail`): numbered citation chips and a drawer that loads
  `GET /citations/{chunk_id}` (full text + provenance: publisher, publication, release date,
  doc type, section, URL, rerank score, expanded flag).
- **Answer confidence** (`ConfidenceReadout`): an honest faithfulness signal — supported vs
  unsupported claims — rather than a single opaque score.
- **History** (`RecentQueries`, `SavedViews`, backed by `lib/persist`): recent and pinned queries
  per screen, stored in the browser.
- **Jobs** (`JobConsole`): progress and live log stream for any background job, with cancel and
  log download.
- **Design system** (`styles/tokens.css`): soft teal token set with light and dark themes, shared
  table, overline and row-highlight styles, score bars, refusal and empty states.

## 4. Area A — Business

### A1.1 Ask — cited Q&A (`features/business/ask/`)

| Part | Detail | Backed by |
|---|---|---|
| Composer | Multiline question + filters | `POST /query/stream` |
| Streaming progress | `status` → `retrieved {n_chunks, route, cached}` → `answer` → `done` | SSE |
| Answer | `summary`, `details[]` with inline citation chips, route and cached badges, confidence readout | `/query` response |
| Refusal | `refused=true` (below the relevance floor) explained, with the filters and route tried | `refused` |
| Caveats | `unanswered[]` (not covered by the corpus) and `unsupported[]` (dropped by the verifier) as distinct callouts | response |
| Evidence rail | Retrieved chunks with rerank-score bars, publisher, release date, doc type, expanded flag | `chunks[]`, `/citations/{id}` |
| Series | Hydrated time-series attachments (source, variable, rows) | `timeseries[]` |
| Feedback | 👍/👎, note, relevant chunks | `POST /feedback` |

The route badge (`DIRECT / PARAPHRASE / DECOMPOSE / DECOMPOSE_AND_PARAPHRASE / HYDE /
PLAN_EXECUTE`) links the business view to the technical one.

### A1.2 Factors — risk-factor discovery (`features/business/factors/`)

The exploration step before synthesis: given a portfolio or theme, rank the controlled macro risk
factors that drive it, each cited to ≥1 dated chunk. Composer with `top_k`, filter editor, ranked
`factor_tag` list with relevance, entity tags and citations, evidence rail, route badge, history.
Backed by `POST /factors`.

### A1.3 Scenario Studio — shock-vector synthesis (`features/business/scenario/`)

Turns a scenario narrative into a **cited, gated** shock vector for faultline.

| Part | Detail | Backed by |
|---|---|---|
| Scenario form | Question, horizon (days), scenario text, optional field subset, filters | `POST /query` with `mode=synthesize` |
| Shock-vector table (`ShockVectorTable`) | One row per vocabulary field: value, unit (`bp`/`pct`/`pts`/`rel`), sign convention, grounding mode, rationale, citations; gated-out fields muted with their reason | `shock_vector`, `GET /synthesis/schema` |
| Diagnostics (`SynthesisDiagnosticsView`) | Why fields were kept, dropped or flagged (citation gate, plausibility band, horizon and direction checks, cross-field coherence) | `diagnostics` |
| World-view stamp | `world_view_id` as a provenance stamp — not a queryable snapshot | response |
| Compare (`ScenarioCompare`) | Two synthesized vectors side by side | client-side |
| Export | Copy or download `{query, world_view_id, shock_vector}` as JSON | client-side |

### A1.4 Corpus Explorer (`features/business/corpus/`)

Facet rail (tier, bucket, publisher, doc type, entity/factor tags, date range), document list,
document drawer (chunks with sequence, section and tags; numeric documents show their series),
and the time-series catalog. Backed by `GET /corpus/facets`, `/corpus/documents`,
`/corpus/documents/{id}`, `/corpus/series`.

### A2 Internal maintenance (`features/maintenance/`)

The UI form of the CLI and maintenance scripts; every mutation runs as a background job.

| Screen | What it does | Backed by |
|---|---|---|
| **Ingestion** | Tier board and endpoint table (handler, bucket, cadence, discovery type, enabled); run a tier or chosen endpoints with `limit` / `refresh`; live job log | `GET /admin/tiers[/{tier}]`, `POST /admin/ingest` |
| **Curation** | Vocabulary viewer/editor; dry-run-capable jobs for vocabulary normalization, rechunking and DFAST/SEP re-extraction | `GET/PUT /admin/vocabulary`, `POST /admin/curate` |
| **Index** | Manifest panel (embedding signature, models, prompt hash, collection, counts), staleness, full rebuild or append | `GET /admin/manifest`, `POST /admin/index` |
| **Quality** | Golden-set viewer; eval runner (hit@k, recall@k, MRR, optional judge); drift vs baseline and baseline swap; self-check; realized-episode backtest (`grounding_fidelity` and `ex_ante_skill` kept separate); feedback inbox | `/admin/golden`, `/admin/eval[/history]`, `/admin/eval/baseline`, `/admin/selfcheck`, `/admin/backtest[/episodes]`, `/admin/feedback` |

## 5. Area B — System (`features/system/`)

### B1 System Map

An annotated component graph from ingest to API (`ingest → processed docs → index pipeline →
{Qdrant, time-series store} → retriever → agent → synthesis/gate → llm → api`), overlaid with the
live config and the active implementation behind each interface (embedder, vector store,
reranker, LLM client — real or fake) from `GET /admin/config`.

### B2 Trace Explorer

A real request through the pipeline, from the JSONL query traces (`GET /admin/traces[/{id}]`).

Stage model (from `query_pipeline`): cache lookup → query analysis → route decision → (plan and
execute, on `PLAN_EXECUTE`) → branch embedding → hybrid search → RRF fusion → rerank → time decay
→ MMR → neighbour expansion → relevance-floor gate → generation → faithfulness verification →
(synthesis + gate) → trace and cache write.

- **Trace list:** time, query, route, refused, latency, cached; filterable.
- **Waterfall** (`Waterfall`): `timings_ms` per stage.
- **Drill-down:** branches, retrieved chunks with scores, filters, hydrated series, token usage,
  escalations, unsupported claims.
- **Score evolution** (`ScoreEvolution`): fused vs rerank score per retained chunk.

### B3 Health

Liveness and manifest summary, index staleness, build history with field-level diffs
(`/admin/manifest/history`), latency, token and cost roll-ups, cache hit rate, route mix, eval and
drift trend, and composed alerts (staleness, drift regressions). Backed by `/healthz`,
`/admin/metrics`, `/admin/health`.

### B4 Stores

Vector store (backend, collection, point count, quantization, payload indexes) with a raw search
probe (pre-rerank candidates under filters); time-series store (observation count, sources,
variables, scenarios) with a query builder; chunk lookup by id. Backed by
`/admin/stores/vector[/search]`, `/admin/stores/timeseries[/query]`, `/citations/{id}`.

## 6. Not built

- A numeric **series chart** — series attachments render as data, not plots, although the API
  returns plottable `series` points.
- A **plan view** for `PLAN_EXECUTE` traces (typed plan, computed values, calculator steps).
- **Full per-stage score evolution** (fusion → rerank → time decay → MMR). The trace keeps final
  and fused/rerank scores only; this needs the retriever to record stage snapshots.
- A **command palette**, a **coverage heatmap**, per-endpoint **overdue** flags on the Ingestion
  screen (the `/admin/tiers` freshness data exists), a **schema-health** panel
  (`/admin/schema-health` exists), and a **feedback-reconcile** action
  (`POST /admin/feedback/reconcile` exists).
- **Incremental index** is CLI-only (`index --incremental`; the admin index job takes only
  `append`), and per-field synthesis **directions** are API/CLI-only (Scenario Studio does not
  send them).

## 7. Stack and layout

- **React 18 + TypeScript + Vite 5**; **TanStack Query** (server state, polling) and **TanStack
  Router**. No chart, graph or headless-component libraries — charts and graphs are hand-built
  SVG/CSS on the token system.
- **SSE over `fetch`** (`lib/sse.ts`) for `/query/stream` and job logs — `fetch` rather than
  `EventSource` so the `X-API-Key` header can be sent.
- **Types:** `api/types.ts` hand-mirrors the Pydantic models and serializers; `npm run gen:api`
  generates `schema.gen.ts` from the live OpenAPI schema for cross-checking.
- **Checks:** `tsc` type-check and production build (CI `ui-build`); there is no UI unit-test
  suite.

```
ui/src/
  app/         shell, router, nav, API-key gate
  api/         client, hand-written types, admin/corpus calls, hooks
  components/  shared primitives (§3)
  features/
    business/     ask/ factors/ scenario/ corpus/
    maintenance/  ingestion/ curation/ index/ quality/
    system/       map/ traces/ health/ stores/
  lib/         sse, persist, citations, download, platform, shock vocab, trace stats
  styles/      tokens.css (light/dark), global.css
public/theme-init.js   theme bootstrap (external so the CSP can forbid inline scripts)
```

## 8. Decisions that matter

1. **Real admin endpoints and a job model**, not a read-only file viewer: maintenance and system
   screens are backed by `/admin/*` and background jobs with SSE logs, guarded by an admin role
   on the API-key auth.
2. **Separate UI container** serving the static build and reverse-proxying `/api` — the same
   bundle runs in dev and prod with no per-environment rebuild
   ([UI containerization](UI_Containerization.md)).
