# 05 — REST API & Integration

This guide is for **operators serving the API** and **engineers integrating another
system** (e.g. the faultline stress engine) with MacroRAG. It covers starting the
server, every endpoint, authentication, the background-job system, and the
**shock-vector synthesis contract**.

> Audience: technical. **This guide is the canonical endpoint reference** — the
> [project README](../../README.md) carries only a summary and links here. The synthesis
> contract is implemented in `src/synthesis/` (`vocab.py` is the authority on field
> names/units/signs; §5 tabulates it).

---

## 1. Start the server

```bash
python -m pipelines.cli serve [--host 127.0.0.1] [--port 8000]
```

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address (`0.0.0.0` to expose externally) |
| `--port` | `8000` | Listen port |

```bash
python -m pipelines.cli serve --host 0.0.0.0 --port 8000
```

The query pipeline is **lazy-loaded on the first request** (models load on demand, so
startup is fast). Interactive OpenAPI docs are at `http://127.0.0.1:8000/docs`, and a
machine-readable schema at `/openapi.json` (the UI's `gen:api` script consumes this).

For production process management (uvicorn workers, Docker), see
[08 – Operations & Deployment](08-operations-and-deployment.md).

---

## 2. Authentication

Auth is **off by default** (open API, no key needed). Enable it in `configs/rag.yaml`:

```yaml
auth:
  enabled: true
  rate_limit_rpm: 60
  api_keys:                      # key -> principal id
    "PUBLIC_KEY_1": "principal-a"
    "ADMIN_KEY_1":  "ops"
  admin_api_keys:                # subset of api_keys allowed to call /admin/*
    - "ADMIN_KEY_1"
```

- **Header:** all authenticated calls send `X-API-Key: <key>`.
- **Public endpoints** require any valid key (and map it to a principal) when auth is on.
- **Admin endpoints** require a key that is also listed in `admin_api_keys`.
- Per-key **rate limiting** (`rate_limit_rpm`) returns `429` when exceeded.
- Invalid/missing key → `401`; non-admin key on `/admin/*` → `403`.
- Other status codes: **402** (daily cost ceiling hit, `cost.*`), **503**
  (`api.max_concurrency` admission control shedding load), **409** (job conflict — a
  mutually-exclusive index/ingest/curate is already running), **422** (request outside
  the documented input bounds) ([Production Hardening §H](../design/Macro_RAG_Production_Hardening.md),
  [§K](../design/Macro_RAG_Production_Hardening.md)).

> **Principal = identity, not isolation.** The corpus is a single shared public
> knowledge base (central-bank/agency/house research). A key's **principal** is used for
> auth, per-key rate limiting and cost attribution; it is stamped onto retrieval as a
> *dormant hook* (`MetaFilter.tenant_id`) but **does not** partition data — every principal
> sees the same corpus, and no ingest path writes that field. There is no private
> per-principal ingest path today; never describe the API as multi-tenant. See
> [Production Hardening §A](../design/Macro_RAG_Production_Hardening.md).
>
> Deprecated aliases are kept for compatibility: config `cost.per_tenant_daily_usd` →
> `per_principal_daily_usd`, and the `--tenant` CLI/MCP flag → `--principal`.

```bash
curl -s http://127.0.0.1:8000/query \
  -H "X-API-Key: PUBLIC_KEY_1" -H "Content-Type: application/json" \
  -d '{"question":"DFAST 2026 severely adverse unemployment path?"}'
```

---

## 3. Public endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | Liveness + whether the index is built + manifest summary |
| `POST` | `/query` | Synchronous cited answer (`mode: answer`) or shock vector (`mode: synthesize`) |
| `POST` | `/query/stream` | Same, streamed as Server-Sent Events |
| `POST` | `/factors` | Ranked, cited macro risk factors relevant to a portfolio/theme |
| `GET` | `/citations/{chunk_id}` | Fetch a cited chunk's text + provenance |
| `POST` | `/citations` | Batch: resolve many `chunk_ids` to texts + provenance in one call |
| `POST` | `/feedback` | Record a thumbs up/down (+ optional note, chunk ids) |
| `GET` | `/corpus/facets` | Corpus aggregates (counts, tiers, tags) |
| `GET` | `/corpus/documents` | Facet-filtered document list (query params: tier, doc_type, entity_tag, factor_tag, date_from/to, q, limit, offset) |
| `GET` | `/corpus/documents/{doc_id}` | Document detail + its chunks + time-series summary |
| `GET` | `/corpus/series` | Time-series catalog grouped by source |
| `GET` | `/vocabulary` | Controlled facet vocabulary (factor/entity tags, doc types, aliases) for building `filters` |
| `GET` | `/synthesis/schema` | Shock-field catalog (valid fields, units, sign conventions) for `mode: synthesize` |
| `GET` | `/metrics` | Prometheus text-exposition scrape target (gated by `observability.metrics_enabled`, default on) ([Production Hardening §J](../design/Macro_RAG_Production_Hardening.md)) |

### `POST /query` request body

```json
{
  "question": "string (required)",
  "mode": "answer",                  // "answer" (default) | "synthesize"
  "filters": {
    "tiers": [1, 2],
    "buckets": ["A", "B", "C"],
    "doc_types": ["dfast_scenarios", "sep_projections"],
    "entity_tags": ["ai", "china", "semiconductors"],
    "factor_tags": ["discount_rate", "credit_spread"],
    "publishers": ["Federal Reserve"],
    "date_from": "2024-01-01",
    "date_to": "2025-12-31"
  },
  "synthesis": {                     // optional; only used when mode = "synthesize"
    "fields": ["UST_10Y", "IG_OAS", "VIX"],   // restrict the shock vector to these
    "horizon_days": 10,                        // pin the scenario horizon structurally
    "scenario": "Taiwan cross-strait blockade", // extra narrative folded into retrieval + synthesis
    "directions": {"UST_10Y": "down", "IG_OAS": "up", "VIX": "up"}  // expected sign per field
  },
  "no_cache": false                  // true: skip the answer caches (neither read nor written)
}
```

All `filters` fields are optional. `no_cache: true` forces a fresh retrieval and answer and
leaves the caches untouched, in both modes and on `/query/stream`. Use it when repeated calls
must vary independently, e.g. to measure synthesis stability; it costs a full uncached request.
The shock vector itself is never cached, and a refusal is never stored.

The answer response includes the prose `summary`,
`details` with `chunk_ids`, hydrated `timeseries` (each carries plottable `series`
points grouped by variable × scenario, [Production Hardening §E](../design/Macro_RAG_Production_Hardening.md)),
the chosen `route`, a `cached` flag, a `chunks` array (each with `chunk_id`,
`source_id`, `publisher`, `publication`, `release_date`, `doc_type`, `rerank_score`,
`expanded`), and a `usage` block with the request's model-call cost.

**`usage`** (both modes, always present):

```json
"usage": {"cost_usd": 0.0123, "cost_complete": true, "calls": 4,
          "prompt_tokens": 9000, "completion_tokens": 800, "cached_tokens": 0,
          "total_tokens": 9800, "cached": false}
```

- `cost_usd` is the summed estimated cost of every LLM call made for this request: query
  analysis, answer generation, verification, escalation retries, and in synthesize mode the
  synthesis pass. Prices come from `configs/llm_pricing.yaml`, which is an estimate, not a bill.
- A request that made no LLM call reports `0.0`, e.g. an answer-mode cache hit. A synthesize
  cache hit still pays for its synthesis pass, because the shock vector is never cached.
- `cost_complete: false` means some call used a model with no price entry; `cost_usd` then
  counts only the priced calls, so it is a lower bound.
- Not included: embedding and reranking. They run on the self-hosted RagInference service,
  which is not a priced call.
- `cached` copies the response's answer-cache flag. The block never carries prompts or
  responses.

### `POST /query/stream` (SSE)

Same body; responses arrive as `event:`-tagged SSE frames (retrieving → retrieved →
answer → done). The UI uses an SSE-over-POST reader for this. The `answer` frame carries the
same `usage` block.

### `POST /feedback`

```json
{ "question": "…", "rating": 1, "note": "optional", "chunk_ids": ["dfast::012"] }
```

`rating` is `1` (up) or `-1` (down). Logged to `data/traces/feedback.jsonl`.
Positive, chunk-citing feedback is turned into **candidate** golden entries by
`POST /admin/feedback/reconcile` (deduped vs the live golden set, written to a
review file) — a human promotes them; nothing is auto-added to the golden set
([04 §5](04-query-retrieval-eval.md)).

### `POST /citations` — batch provenance

A synthesized shock vector or factor list cites many chunks; resolving them one at
a time via `GET /citations/{id}` means N round-trips. Send them all at once:

```json
{ "chunk_ids": ["bis_rule::000", "fsr::012", "missing::999"] }
```

```json
{
  "citations": [
    { "chunk_id": "bis_rule::000", "text": "...", "publisher": "...",
      "release_date": "2026-05-13", "url": "...", "section": "..." }
  ],
  "missing": ["missing::999"]
}
```

`citations` preserves request order and is deduped; any unresolved id is listed in
`missing` (no `404` — a partial set still returns `200`). Capped at 200 ids/call.

### `POST /factors` — macro risk-factor discovery

The **exploration step that precedes synthesis**: given a portfolio/theme, return
the macro risk factors worth stress-testing — ranked, cited, and drawn from the
controlled `factor_tags` vocabulary so each one feeds straight back as a `/query`
filter (or into a `mode: synthesize` call).

```json
{
  "portfolio": "AI-themed equities: hyperscalers + semiconductor supply chain, China-exposed",
  "filters": { "entity_tags": ["ai", "semiconductors", "china"] },
  "top_k": 8
}
```

`filters` and `top_k` (1–50, default 8) are optional. Response:

```json
{
  "factors": [
    { "factor_tag": "export_controls", "relevance": "high",
      "entity_tags": ["china", "semiconductors"],
      "chunk_ids": ["bis_rule::000"], "rationale": "BIS rule tightens AI-chip exports" },
    { "factor_tag": "china_taiwan_tail", "relevance": "medium",
      "entity_tags": ["china"], "chunk_ids": ["fsr::012"] }
  ],
  "route": "DIRECT",
  "chunks": [ { "chunk_id": "bis_rule::000", "release_date": "2026-05-13", "...": "..." } ],
  "world_view_id": "macro-2026-06-23-9f2c1ab77e04"
}
```

Like synthesis, it is **citation-gated**: a factor is emitted only if it cites ≥1
chunk that is present in `chunks[]` with a `release_date`. Off-vocabulary tags are
dropped. No prose answer is generated, so it is cheaper than `/query`. Typical
flow: `POST /factors` to find the drivers → `POST /query` (`mode: synthesize`,
`synthesis.fields` = the discovered tags' shock fields) to quantify them.

### Discovery (`GET /vocabulary`, `GET /synthesis/schema`)

These let an **LLM-driven consumer** discover the valid inputs before it calls
`/query`, without needing an admin key.

`GET /vocabulary` returns the controlled facet vocabulary used by `filters`:

```json
{
  "factor_tags": ["credit_spread", "discount_rate", "equity_vol", "fx", "..."],
  "entity_tags": ["ai", "china", "semiconductors", "..."],
  "doc_types": ["dfast_scenarios", "sep_projections", "..."],
  "aliases": { "tsmc": "semiconductors", "taiwan": "china", "...": "..." }
}
```

`GET /synthesis/schema` returns the shock-field catalog consumed by
`mode: synthesize` — the only valid field names, their allowed/preferred units,
and the positive-sign convention:

```json
{
  "fields": [
    { "name": "UST_10Y", "units": ["bp"],  "preferred_unit": "bp",  "positive_means": "yields rise" },
    { "name": "IG_OAS",  "units": ["bp"],  "preferred_unit": "bp",  "positive_means": "spreads widen" },
    { "name": "VIX",     "units": ["pts"], "preferred_unit": "pts", "positive_means": "equity vol rises" },
    { "name": "USDTWD",  "units": ["pct"], "preferred_unit": "pct", "positive_means": "TWD depreciates" }
  ]
}
```

A consumer should treat these as the source of truth: build `filters` only from
`/vocabulary` tags, and map any returned `shock_vector` field against
`/synthesis/schema` (units + sign) rather than hard-coding the table.

---

## 4. Admin endpoints (admin key required)

Read surfaces:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/admin/manifest` | Index manifest, staleness, `world_view_id`, pipeline version |
| `GET` | `/admin/manifest/history` | Append-only build log — every index build with a **field-level diff** against the previous one ([03 §4](03-indexing.md)) |
| `GET` | `/admin/manifest/verify?world_view=…` | For an old shock vector: *was this ever our corpus, and is it still live?* |
| `GET` | `/admin/config` | Sanitized config + active implementations (no secrets) |
| `GET` | `/admin/health` | Dependency/health detail behind the public `/healthz` |
| `GET` | `/admin/metrics` | Per-trace roll-up for the UI (the scalable production path is the public Prometheus `/metrics`) |
| `GET` | `/admin/traces` · `/admin/traces/{idx}` | Query traces (paginated / single) |
| `GET` | `/admin/stores/vector` · `/admin/stores/timeseries` | Store stats |
| `POST` | `/admin/stores/vector/search` | Raw pre-rerank embedding-search probe (inspect candidates + filters) |
| `GET` | `/admin/stores/timeseries/query` | Query the time-series store directly (source / variable / scenario) |
| `GET` `PUT` | `/admin/vocabulary` | Controlled vocab (tags, doc types, aliases); `PUT` edits **and persists** it ([Production Hardening §E](../design/Macro_RAG_Production_Hardening.md)) |
| `GET` | `/admin/schema-health` | Structured-output health per LLM task and model — ok / repaired / failed / refused counts, `truncated` (replies cut off at the output cap) and `repair_rate` ([Production Hardening §B](../design/Macro_RAG_Production_Hardening.md)) |
| `GET` | `/admin/tiers` · `/admin/tiers/{name}` | Ingest tier catalog + endpoints, with per-tier corpus-freshness fields (last-ingested, `days_since_ingest`, doc count) ([Production Hardening §E](../design/Macro_RAG_Production_Hardening.md)) |
| `GET` | `/admin/golden` | Retrieval golden set |
| `GET` | `/admin/feedback` | Feedback log (paginated) |
| `POST` | `/admin/feedback/reconcile` | Write deduped **REVIEW** golden candidates from positive feedback (never auto-promoted) ([Production Hardening §B](../design/Macro_RAG_Production_Hardening.md)) |
| `GET` | `/admin/eval/history` | Past eval runs |
| `GET` | `/admin/eval/baseline` | Drift baseline; `POST` to save a new one |
| `GET` | `/admin/backtest/episodes` | The realized-episode catalog + the reference moves derived for each ([04 §5](04-query-retrieval-eval.md)) |

Job-launching endpoints (return a job immediately; run in the background):

| Method | Path | Body | Equivalent CLI |
|---|---|---|---|
| `POST` | `/admin/index` | `{"append": false, "incremental": false, "dry_run": false}` — `incremental` runs the diff-based refresh, `dry_run` (with `incremental`) only reports its plan; `dry_run` alone or `incremental` + `append` → **422** | `pipelines.cli index [--append \| --incremental [--dry-run]]` |
| `POST` | `/admin/eval` | `{"k": 8, "judge": false}` | `pipelines.cli eval --k 8 [--judge]` |
| `POST` | `/admin/selfcheck` | `{}` | **none** — admin-only ([04 §5](04-query-retrieval-eval.md)) |
| `POST` | `/admin/backtest` | `{"episodes": ["…"]}` (omit for the whole catalog) | **none** — admin-only ([04 §5](04-query-retrieval-eval.md)) |
| `POST` | `/admin/ingest` | `{"tier": "tier1_fed", "endpoints": ["press_monetary"], "limit": 5, "refresh": false}` | `python -m ingest …` |
| `POST` | `/admin/curate` | `{"task": "normalize-vocab", "dry_run": true}` | `scripts/*.py` |

> `tier` for `/admin/ingest` uses the **full stem** (`tier1_fed`, `tier2_treasury`, …),
> same as the CLI. `task` for `/admin/curate` is one of `normalize-vocab`, `rechunk`,
> `reextract-sep`, `reextract-dfast` (the last accepts `{"strip_only": true}`).
>
> **`selfcheck` and `backtest` have no CLI equivalent** — the deterministic
> self-validation harness and the realized-episode backtest are reachable only here.
> What they measure, and why the two shock-accuracy families are never averaged, is in
> [04 §5](04-query-retrieval-eval.md).

### The job system

`POST /admin/{index,eval,selfcheck,backtest,ingest,curate}` returns a job descriptor (`id`, `type`,
`status: queued`). Track it:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/admin/jobs` | List jobs (status, type, timestamps) |
| `GET` | `/admin/jobs/{id}` | Job detail incl. `result`, `error`, captured `logs` |
| `GET` | `/admin/jobs/{id}/stream` | Stream the job's logs live (SSE) |
| `POST` | `/admin/jobs/{id}/cancel` | Request cooperative cancellation of a running job |

> **Single-writer + idempotency.** `index`, `ingest` and `curate` are **mutually
> exclusive** — a concurrent submit while one is running returns **HTTP 409
> (JobConflict)**. An optional `idempotency_key` on the submit dedups retried requests to
> the same job. The job store/lock is Redis-backed when `jobs.backend: redis`, else
> in-process ([Production Hardening §H](../design/Macro_RAG_Production_Hardening.md),
> [§I](../design/Macro_RAG_Production_Hardening.md)).

```bash
# launch an incremental ingest, then watch it
JOB=$(curl -s -X POST localhost:8000/admin/ingest -H "X-API-Key: ADMIN_KEY_1" \
      -H "Content-Type: application/json" \
      -d '{"tier":"tier1_fed","limit":5}' | jq -r .id)
curl -s -N localhost:8000/admin/jobs/$JOB/stream -H "X-API-Key: ADMIN_KEY_1"
```

This is how the UI's Maintenance area drives long-running tasks with live logs — and
how you'd script a scheduled re-ingest/re-index without shelling into the host.

---

## 5. Integrating another subsystem (faultline) — shock-vector synthesis

The headline integration: faultline asks MacroRAG to turn a macro scenario into a
**machine-readable, citation-gated shock vector** for its stress engine.

Call `POST /query` (or `/query/stream`) with `mode: synthesize`:

```bash
curl -s http://127.0.0.1:8000/query \
  -H "X-API-Key: PUBLIC_KEY_1" -H "Content-Type: application/json" \
  -d '{
    "question": "Quantify 10-day shocks to USD/TWD, IG spreads and VIX from a Taiwan cross-strait escalation",
    "mode": "synthesize",
    "filters": { "entity_tags": ["china"], "factor_tags": ["fx","credit_spread","equity_vol"] }
  }'
```

For an automated flow, prefer the structured `synthesis` controls over encoding
everything in prose. Instead of *"quantify the 10-day shocks to USD/TWD, IG and
VIX from a Taiwan blockade"*, send a generic question plus:

```json
{
  "question": "quantify the macro shocks for this scenario",
  "mode": "synthesize",
  "synthesis": {
    "fields": ["USDTWD", "IG_OAS", "VIX"],
    "horizon_days": 10,
    "scenario": "Taiwan cross-strait blockade and export-control escalation",
    "directions": {"USDTWD": "up", "IG_OAS": "up", "VIX": "up"}
  }
}
```

- `fields` — the shock vector is restricted to this subset (validated against
  `GET /synthesis/schema`; an unknown name returns `400`). The citation gate still
  applies, so a requested field with no dated source is simply absent.
- `horizon_days` — rendered into the synthesis prompt as the cumulative horizon, and
  checked against the horizon each field's evidence covers (`horizon_mismatch`, see Field
  validation below). A field may carry an optional `source_horizon_days`: the horizon, in
  days, the model says its cited evidence covers.
- `scenario` — folded into the query for **both** retrieval and synthesis.
- `directions` — the sign the scenario expects for each field: `"up"` means a positive value
  under the field's *Positive means* convention (table below), `"down"` a negative one. A
  field whose value has the other sign gets `direction_mismatch` (see Field validation below);
  zero never counts as the wrong sign. The directions are also stated to the model in the
  synthesize prompt, so it chooses evidence that moved that way rather than only
  having wrong-way fields dropped. Keys follow the same rules as `fields`: case-insensitive,
  an unknown name returns `400`, and a value other than `up`/`down` returns `422`. State
  directions whenever the scenario has them: the model can otherwise ground a field on an
  analog that moved the other way (e.g. a TWD-strengthening episode for a TWD-weakening
  scenario).

The response contains everything an `answer` returns **plus**:

```json
{
  "shock_vector": {
    "USDTWD": { "value": 8.0,  "unit": "pct", "chunk_ids": ["bis::045"], "rationale": "…" },
    "IG_OAS": { "value": 90.0, "unit": "bp",  "chunk_ids": ["fsr::012"] },
    "VIX":    { "value": 18.0, "unit": "pts", "chunk_ids": ["fsr::012"] }
  },
  "world_view_id": "macro-2026-06-23-9f2c1ab77e04"
}
```

### The contract (`src/synthesis/`)

- **Fixed 19-field vocabulary** (canonical list: `src/synthesis/vocab.py`) — only these
  field names are valid, each with fixed allowed units and a documented positive-sign
  convention:

  | # | Field | Unit | Positive means |
  |---|---|---|---|
  | 1–5 | `UST_2Y` `UST_5Y` `UST_10Y` `UST_30Y` `UST` | `bp` | yields rise (`UST` = parallel shift) |
  | 6–7 | `IG_OAS` `HY_OAS` | `bp` | spreads widen |
  | 8 | `MOVE` | `rel` (or `pts`) | rates vol rises (aggregate) |
  | 9–12 | `MOVE_1Y` `MOVE_5Y` `MOVE_10Y` `MOVE_30Y` | `rel` (or `pts`) | rates vol rises (by swaption expiry) |
  | 13 | `VIX` | `pts` | equity vol rises |
  | 14 | `EQUITY_US` | `pct` | equity index **rises** (a broad US equity *level*, not vol) |
  | 15 | `USDTWD` | `pct` | TWD depreciates |
  | 16 | `USDJPY` | `pct` | JPY depreciates |
  | 17 | `XCCY_BASIS` | `bp` | — |
  | 18 | `REAL_YIELD_10Y` | `bp` | the 10Y TIPS real yield rises |
  | 19 | `TERM_PREMIUM_10Y` | `bp` | the ACM 10Y term premium rises (NY Fed model, not Kim–Wright) |

  Unknown fields or units are **rejected**. `GET /synthesis/schema` returns this same table
  machine-readably — a consumer should map against that rather than hard-coding it.

  > `EQUITY_US` covers the equity leg, which is where an AI-themed book's P&L actually sits
  > (the rest of the envelope is rates/credit/vol/FX). It is grounded in the DFAST `dow_jones` path across the three
  > scenarios plus 156 quarters of realized history, so it backtests on the same machinery as
  > everything else. Single-name and sector dispersion stay out of scope — that is faultline's
  > bottom-up methodology, not this contract.

  > `REAL_YIELD_10Y` and `TERM_PREMIUM_10Y` (spec `vocab-rates-decomp`) split a
  > nominal rates move into its real-yield and term-premium parts. The market-episode cards
  > measure both: the real yield from FRED `DFII10`, the term premium from the NY Fed **ACM**
  > model (`ACMTP10`). Kim–Wright stays on the cards as a label only, because it is a different
  > model and its moves can differ a lot (August 2023 refunding: ACM +17 bp, Kim–Wright +10 bp).

  > **`MOVE` and its expiry nodes are not measured by MacroRAG.** The ICE
  > MOVE index is proprietary and not on FRED, so no market-episode card carries it
  > (`missing_fields` reports `card_field: false`). A `MOVE*` field is emitted only when prose
  > evidence quantifies a rates-vol move at a horizon that passes `horizon_mismatch`; at
  > 5–10-day horizons that is rare. A consumer that needs short-horizon rates vol should take it
  > from its own MOVE history (faultline already pulls `^MOVE` daily for its swaption and cap vega
  > shock) and may omit `MOVE*` from `synthesis.fields`. The fields stay in the contract for
  > longer-horizon, prose-grounded scenarios.

- **Citation gate** (`src/synthesis/gate.py`) — every emitted field must cite at least
  one chunk, and each cited chunk must carry a `release_date`. Fields whose citations
  fail the gate are **dropped**, not guessed. This is what makes the vector
  trustworthy for downstream stress testing.

- **Field validation** (`src/synthesis/validate.py`, on while `synthesis.numeric_validation`
  is) — a kept field may carry `validation` codes, and each also appears with context in
  `synthesis_diagnostics.validation`. Codes marked *hard* are removed when
  `synthesis.drop_invalid` is on, and then appear in `synthesis_diagnostics.dropped`. The
  committed `configs/rag.yaml` turns it on; the code default (and any config that omits
  the key) is annotate-don't-drop.

  | Code | Hard | Meaning |
  |---|---|---|
  | `implausible_magnitude` | yes | the move exceeds the field's horizon-scaled plausibility band. The band is a quarterly cap scaled by √(horizon / 90), but never below a floor share of the cap for fields that jump (VIX 1.0, Treasuries 0.5), so realized 5–10-day spikes pass |
  | `unit_scale_mismatch` | yes | the value copies a cited number's digits across a unit change, e.g. "1 percent" emitted as 1bp |
  | `exceeds_observed_range` | no | the move dwarfs the hydrated series' observed span |
  | `horizon_mismatch` | yes | the evidence covers a horizon more than `synthesis.horizon_tolerance` (default 3) times away from the requested `horizon_days`, e.g. a quarterly DFAST path used as a 10-day shock |
  | `horizon_unknown` | no | neither the cited chunks nor the model say what horizon the evidence covers |
  | `direction_mismatch` | yes | the value's sign contradicts the caller's `directions` for the field (only when `directions` is sent and `synthesis.direction_check` is on, the default) |
  | `card_value_mismatch` | yes | the field cites a market-episode card that measures it but does not carry that card's largest move (within half the card's last printed decimal), e.g. the end value taken to fit a direction |
  | `fx_proxy_unlisted` | no | an analog FX shock is anchored only on currencies outside the field's allowlist (`synthesis/vocab.py`), e.g. UAH for TWD |
  | `episode_mixed` | no | the vector's fields cite more than one market-episode card, so its moves did not happen together; set on every card-citing field, with an `episode_mixed` warning mapping episode → fields |
  | coherence codes (e.g. `credit_indices_diverge`), `rates_vol_term_inconsistent` | no | fields disagree in sign with each other |

  `synthesis_diagnostics.outcome` (`grounded` / `partial` / `all_dropped` / `llm_empty` /
  `no_chunks` / `upstream_refused`) and `coverage_gap` say why a vector is empty or thin.
  A consumer should refuse to price a vector with `coverage_gap: true`, and should down-weight
  or drop flagged fields.

  `synthesis_diagnostics.missing_fields` explains each field requested in `synthesis.fields`
  that is not in the vector, in request order: `{"field", "reason", "codes", "card_field"}`.
  `reason` is `dropped` (with the drop's `codes`) or `not_emitted` (the model found no evidence
  it could use, e.g. a card's move went against the requested direction). `card_field` says
  whether any market-episode card can measure the field at all: `MOVE` and its expiry nodes,
  `EQUITY_US`, `XCCY_BASIS` and `UST` cannot, so they can only come from prose. Without
  `synthesis.fields` the list is empty.

- **Market-episode citations** — a field may cite a chunk with `doc_type: market_episode`,
  publisher `MacroRAG (derived from FRED)`: the realized move over 5 or 10 trading days after a
  named stress episode, computed from FRED daily data ([02 §4](02-data-ingestion.md#fred-daily-series-and-market-episode-cards)).
  Its `release_date` is the **end of the measurement window** (e.g. 2025-02-07 for the
  DeepSeek card), so the chunk's age says nothing about whether it applies. A consumer that
  gates evidence on freshness should judge these chunks by `doc_type`, not by age. A
  `publishers` filter that omits this publisher excludes every card.

- **`world_view_id`** *(canonical definition — other docs point here)* — a **freshness /
  provenance stamp** identifying the corpus state the vector was synthesized against, taken
  from the index manifest ([03 §4](03-indexing.md)). Format
  `macro-<YYYY-MM-DD>-<12-hex digest>`, **content-addressed** over the sorted
  `(chunk_id, content_hash)` pairs of the whole corpus. So: an unchanged corpus re-indexed
  keeps the **same** id, and two different corpora built the same day cannot collide.

  Record it alongside the vector. Two admin endpoints make it auditable after the fact:
  `GET /admin/manifest/history` (every build, with a field-level diff against the previous)
  and `GET /admin/manifest/verify?world_view=…` (*was this ever our corpus, and is it still
  the live one?*).

  **It is a stamp with an audit trail, not a queryable snapshot store:** re-indexing
  *replaces* the live corpus, so you cannot retrieve or re-synthesize *against* a prior
  `world_view_id` once superseded — the history proves what that corpus contained, it does
  not let you query it. That is deliberate: the downstream consumer does not replay against
  superseded corpora, so the storage and retention cost of an immutable snapshot store is
  not warranted. If point-in-time replay ever becomes a
  requirement, the design for it is written up in
  [Production Hardening §F](../design/Macro_RAG_Production_Hardening.md).

### Recommended consumer pattern

```python
import os, requests

r = requests.post(
    f"{MACRORAG_URL}/query",
    headers={"X-API-Key": os.environ["MACRORAG_API_KEY"]},
    json={"question": SCENARIO, "mode": "synthesize",
          "filters": {"entity_tags": ["china"], "factor_tags": ["fx", "credit_spread"]},
          "synthesis": {"horizon_days": 10,     # nested here; a top-level key is ignored
                        "directions": {"USDTWD": "up", "IG_OAS": "up", "VIX": "up"}}},
    timeout=300,
)
res = r.json()
diag = res["synthesis_diagnostics"]
if diag["coverage_gap"]:                 # nothing grounded: do not price, fall back / refuse
    raise RuntimeError(f"no grounded shock fields ({diag['outcome']})")

# every cited chunk must be present and dated (defence-in-depth; the server already gates)
dated = {c["chunk_id"] for c in res["chunks"] if c.get("release_date")}
HARD = {"implausible_magnitude", "unit_scale_mismatch", "horizon_mismatch",
        "direction_mismatch"}
shocks = {}
for field, f in res["shock_vector"].items():
    if all(cid in dated for cid in f["chunk_ids"]) and not HARD & set(f.get("validation", [])):
        shocks[field] = {"value": f["value"], "unit": f["unit"],
                         "flags": f.get("validation", [])}   # soft flags: down-weight

world_view = res.get("world_view_id")   # record for provenance/freshness (not a queryable snapshot); see Production Hardening §F
```

For narrative Q&A (not stress inputs), use the same endpoint with the default
`mode: answer` and read `summary` + `chunks`.

---

## 6. MCP server (for LLM-agent consumers)

When the consumer is an **LLM agent that selects tools** (rather than a fixed
pipeline), MacroRAG also ships an **MCP server** — a thin, self-describing facade
over the *same* query pipeline. It runs **in-process** (no HTTP hop) and its tools
reuse the REST serializers, so the MCP and REST contracts never drift.

```bash
python -m mcp_server                      # stdio (local subprocess integration)
python -m mcp_server --transport sse      # or expose over a port
python -m pipelines.cli mcp               # same thing via the CLI
python -m mcp_server --principal scenariogen   # tag calls with a caller identity (auth/rate-limit only; corpus is shared)
```

| Tool | Mirrors | Returns |
|---|---|---|
| `macro_query(question, filters?, no_cache?)` | `POST /query` (answer) | cited prose summary + chunks + `usage` |
| `macro_synthesize_shock_vector(question, fields?, horizon_days?, scenario?, filters?, directions?, no_cache?)` | `POST /query` (synthesize) | gated `shock_vector` + `world_view_id` + `synthesis_diagnostics` + `usage` (identical to REST) |
| `macro_discover_factors(portfolio, top_k?, filters?)` | `POST /factors` | ranked, gated factor list |
| `macro_get_citations(chunk_ids)` | `POST /citations` | `citations` + `missing` |
| `macro_vocabulary()` | `GET /vocabulary` | controlled facet tags |
| `macro_shock_schema()` | `GET /synthesis/schema` | shock-field catalog |

The agent's typical path: `macro_discover_factors` (find the drivers) →
`macro_shock_schema` (map factors to shock fields) → `macro_synthesize_shock_vector`
with `fields` (quantify) → `macro_get_citations` (verify provenance). An unknown
shock field raises a tool error listing the valid set (same rule as the REST `400`).

> The MCP server uses its own `--principal` flag instead of per-call API keys: it is an
> **auth / rate-limit identity, not data isolation** — the corpus is shared and every
> principal sees the same data (see [§2](#2-authentication);
> [Production Hardening §A](../design/Macro_RAG_Production_Hardening.md)). Front it with your
> transport's auth (stdio runs as a trusted local subprocess).

### Configuring an MCP client (how a consumer discovers the tools)

A consuming agent does **not** hard-code these endpoints. Discovery happens in two
layers:

- **Tool discovery** — the MCP `tools/list` call returns every tool's name,
  description, and JSON-Schema inputs (FastMCP generates the schema from the tool
  signatures). The client maps that straight into its LLM's tool-calling format, so
  adding a tool here surfaces in the consumer with no code change on its side.
- **Argument discovery** — the LLM calls `macro_vocabulary` / `macro_shock_schema`
  at runtime to learn the valid facet tags and shock fields before passing them.
  Discover them up front (or cache per session); the server also rejects unknown
  values, but discovering avoids the round-trip.

**stdio** is the simplest wiring: the consumer spawns this server as a subprocess.
Point it at the `py312` interpreter with `src` on the path (the de-facto
`mcpServers` config shape):

```json
{
  "mcpServers": {
    "macro-rag": {
      "command": "/path/to/envs/py312/python",
      "args": ["-m", "mcp_server", "--transport", "stdio", "--principal", "scenariogen"],
      "cwd": "/path/to/MacroRAG",
      "env": { "PYTHONPATH": "/path/to/MacroRAG/src" }
    }
  }
}
```

For a long-running / cross-host deployment use `--transport streamable-http` (or
`sse`) and have the client connect by URL instead of spawning a subprocess. The
raw `mcp` Python SDK (`stdio_client` + `ClientSession.initialize()` →
`list_tools()` → `call_tool(...)`) works directly; most frameworks wrap this —
LangChain/LangGraph via `langchain-mcp-adapters`, and the Anthropic / OpenAI APIs
via their native MCP connector (hand it the server; it runs `tools/list` for you).

A consumer that prefers REST over MCP can instead generate tool definitions from
the OpenAPI document at `/openapi.json` (browsable at `/docs`) — the same contract,
but the tool-selection layer is then the consumer's to build/regenerate.

---

## 7. Environment & config that affect serving

| Setting | Where | Effect |
|---|---|---|
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | `.env` | LLM provider credentials |
| `llm.provider` / `llm.profiles` | `configs/rag.yaml` | Provider + per-provider tiered model routing (a flat `*_model` key overrides one tier) ([04 §4](04-query-retrieval-eval.md)) |
| `qdrant.url` / `qdrant.path` | `configs/rag.yaml` | Embedded vs server vector store |
| `auth.*` | `configs/rag.yaml` | Enable keys, principals, rate limits, admin keys |
| `cache.*`, `semantic_cache.*` | `configs/rag.yaml` | Query caching (memory or redis) |
| `observability.otel_*` | `configs/rag.yaml` | OpenTelemetry tracing ([08](08-operations-and-deployment.md)) |
| `observability.metrics_enabled` | `configs/rag.yaml` | Expose Prometheus `GET /metrics` (default on, [§J](../design/Macro_RAG_Production_Hardening.md)) |
| `api.max_concurrency` | `configs/rag.yaml` | In-flight admission control; over the limit → `503` ([§L](../design/Macro_RAG_Production_Hardening.md)) |
| `api.cors_allow_origins` | `configs/rag.yaml` | First-class CORS allow-list (see §8) |
| `redis.url` (`MACRORAG_REDIS_URL`) | `configs/rag.yaml` / env | Shared-state Redis URL feeding the per-feature backends below ([§I](../design/Macro_RAG_Production_Hardening.md)) |
| `jobs.backend` | `configs/rag.yaml` | Job store/lock: `redis` (shared) or in-process ([§I](../design/Macro_RAG_Production_Hardening.md)) |
| `auth.rate_limit_backend` | `configs/rag.yaml` | Rate-limit buckets: `redis` or in-process |
| `semantic_cache.backend` | `configs/rag.yaml` | Semantic cache: `redis` or in-process |
| `cost.*` | `configs/rag.yaml` | Daily LLM cost ceiling + counters; over the cap → `402` ([§K](../design/Macro_RAG_Production_Hardening.md)) |
| `alerting.*` | `configs/rag.yaml` | Slack-webhook alerts (cost, regression, staleness) ([§K](../design/Macro_RAG_Production_Hardening.md)) |

---

## 8. Troubleshooting (API / integration)

| Symptom | Likely cause | Fix |
|---|---|---|
| `/healthz` shows `built: false` after a query | Pipeline loads lazily on first request | Send one query; subsequent calls are warm |
| `401 invalid or missing API key` | Auth enabled, key missing/wrong | Send `X-API-Key`; verify it's in `auth.api_keys` |
| `403` on `/admin/*` | Key valid but not an admin key | Add it to `auth.admin_api_keys` |
| `429 rate limit exceeded` | Per-key RPM exceeded | Back off; raise `auth.rate_limit_rpm` |
| `402 cost ceiling reached` | Daily LLM spend cap hit (`cost.*`) | Wait for the daily reset or raise the cap; watch `alerting.*` ([Production Hardening §K](../design/Macro_RAG_Production_Hardening.md)) |
| `503` on a query | Concurrency admission control (`api.max_concurrency`) shed load | Retry with backoff; raise `api.max_concurrency` if the box can take it |
| `409 JobConflict` | An index/ingest/curate job is already running | Wait for it (`/admin/jobs`), or dedup via `idempotency_key` ([Production Hardening §H](../design/Macro_RAG_Production_Hardening.md)) |
| `422` on a request | Input outside bounds (oversized batch / `top_k` etc.) | Fix the request to the documented bounds |
| `shock_vector` missing fields you expected | Citation gate dropped them (no dated source) | Ingest the supporting source; check `chunks[].release_date`; widen `filters` |
| Synthesis returns prose but empty `shock_vector` | Query not answerable from corpus / refused | Rephrase; ensure relevant tiers ingested + indexed |
| Job stuck in `queued`/`running` | Long ingest/index, or it errored silently | Stream `/admin/jobs/{id}/stream`; check `error` field |
| `/admin/ingest` → `unknown tier` | Used a short name | Use full stem `tier1_fed` etc. |
| SSE stream cuts off in a proxy | Buffering proxy | Disable response buffering for `/query/stream` and `/admin/jobs/*/stream` |
| CORS errors from a browser client | Cross-origin without an allow-list or proxy | Set `api.cors_allow_origins` (first-class CORS config), use the UI's Vite proxy ([06](06-ui.md)), or front the API with a reverse proxy |

Next: the web UI → [06 – Web UI](06-ui.md).
