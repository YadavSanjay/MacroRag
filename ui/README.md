# MacroRAG UI

React + TypeScript + Vite web UI for MacroRAG, over the FastAPI backend.

- **Operating it** (install, run, API-key gate, troubleshooting): [docs/guides/06-ui.md](../docs/guides/06-ui.md)
- **Working on it** (toolchain, structure, conventions, adding a screen): [ui/CLAUDE.md](CLAUDE.md)
- **Design & IA**: [docs/design/Macro_RAG_UI_Design.md](../docs/design/Macro_RAG_UI_Design.md)
- **Container/deploy**: [docs/design/UI_Containerization.md](../docs/design/UI_Containerization.md) · [guide 09](../docs/guides/09-packaging-and-deployment.md)

## Screens

Every nav screen is live. `ui/src/` is the source of truth for what exists.

| Area | Screens |
|---|---|
| **Business** | Ask (streaming cited Q&A) · Scenario Studio (shock-vector synthesis, gating view, compare, export) · Factors (ranked cited risk factors) · Corpus (facet browser, documents, series catalog) |
| **Internal maintenance** | Index · Quality (eval + LLM judge, drift, self-check, episode backtest, structured-output health, feedback → golden candidates) · Ingestion (tier catalog + runs) · Curation (vocabulary + maintenance jobs) — long tasks run as background jobs with live SSE logs |
| **System (technical)** | Map (component topology + live config/impls) · Traces (waterfall, score evolution, evidence drill-down) · Health (manifest, staleness, activity, alerts) · Stores (vector + time-series inspectors) |

Not built: a series chart (the API already returns plottable `series` points), full per-stage
score evolution (the trace shows fused vs rerank only), a plan view for `PLAN_EXECUTE` traces and
a command palette — see [the design doc §6](../docs/design/Macro_RAG_UI_Design.md).

## Stack

- React 18 + TypeScript + Vite
- TanStack Query (server state) + TanStack Router (code-based routes)
- SSE-over-POST reader for `/query/stream` and job-log streams
- Plain CSS design tokens, light **and** dark themes, no UI-kit dependency

## Develop

Use the pinned Node toolchain and run from the **repo root** with `--prefix ui` — see
[ui/CLAUDE.md](CLAUDE.md) for why:

```bash
# put the pinned node 24.14.0 toolchain on PATH first
npm install --prefix ui
npm run dev --prefix ui        # http://localhost:5173
```

The dev server proxies `/api/*` to the backend (default `http://127.0.0.1:8000`, override
with `VITE_API_TARGET`) and strips the `/api` prefix. Start the backend first:

```bash
python -m pipelines.cli serve            # from the repo root
```

Other scripts:

```bash
npm run build --prefix ui        # tsc + production build (also the type gate — CI runs this)
npm run typecheck --prefix ui    # tsc only
npm run gen:api --prefix ui      # regenerate the typed client from the live OpenAPI schema
```

## Layout

```
src/
  app/         shell, router, nav registry, top bar, API-key gate
  api/         client.ts, hooks.ts, admin.ts, corpus.ts, types.ts (+ generated schema.gen.ts)
  components/  shared UI (citations, evidence rail, confidence readout, job console, …)
  features/
    business/     ask · scenario · factors · corpus
    maintenance/  index · ingestion · quality · curation
    system/       map · traces · health · stores
  lib/         sse, shockVocab (mirrors src/synthesis/vocab.py), persist, download, traceStats
  styles/      global tokens
public/        theme-init.js — external (not inline) so the container's strict CSP holds
nginx/         default.conf.template — static + /api proxy + SSE, templated by API_UPSTREAM
```

## Backend surface it uses

Public: `POST /query`, `POST /query/stream`, `POST /factors`, `GET|POST /citations`,
`POST /feedback`, `GET /healthz`, `GET /corpus/*`, `GET /vocabulary`,
`GET /synthesis/schema`. Maintenance and System use `/admin/*` plus the job API and need an
admin key. Full reference: [guide 05](../docs/guides/05-api-and-integration.md).
