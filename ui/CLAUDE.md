# ui — React web UI

React + Vite + TypeScript front-end over the FastAPI backend. **`ui/src/` is the source of
truth for what exists.** Design/IA rationale (a dated design record, not a live reference):
[docs/design/Macro_RAG_UI_Design.md](../docs/design/Macro_RAG_UI_Design.md); operator setup:
[docs/guides/06-ui.md](../docs/guides/06-ui.md); status + stack: [ui/README.md](README.md);
the backend contract: [docs/guides/05-api-and-integration.md](../docs/guides/05-api-and-integration.md).

## Toolchain (pinned)

The pinned host toolchain is for **local dev only** — node 24.14.0 / npm 11.9.0. Its
install path is host-local; put it on `PATH` from your own environment. Run from the
**repo root** with `--prefix ui` (do not `cd ui`):
```
npm install --prefix ui
npm run dev --prefix ui        # http://localhost:5173 ; proxies /api -> 127.0.0.1:8000
npm run build --prefix ui      # tsc + vite build   (run this to verify types compile)
npm run typecheck --prefix ui  # tsc --noEmit
```
Dev requires the backend running (`python -m pipelines.cli serve`). Vite proxies `/api/*` to
the backend (strips the `/api` prefix) — see `vite.config.ts` (`VITE_API_TARGET` overrides).

## Container / deploy

The UI ships as its own image (`ui/Dockerfile`): a multi-stage build (official
`node:24.14.0-alpine` → `nginx-unprivileged`) that serves the static `dist/` and
**reverse-proxies `/api/*` to the backend, stripping `/api`** — reproducing the dev Vite
proxy, so the bundle's relative `/api` base (`src/api/client.ts`) works unchanged in prod.
The proxy target is the single runtime env var **`API_UPSTREAM`** (templated into
`ui/nginx/default.conf.template` by nginx's `envsubst` entrypoint); no backend URL is baked
into the JS. SSE works through the proxy (`proxy_buffering off`). Compose/Helm wiring +
the two-image topology (lightweight backend + external RagInference) live in
[infra/](../infra/README.md); the design writeup is
[docs/design/UI_Containerization.md](../docs/design/UI_Containerization.md).

The nginx config sets a strict CSP; the theme bootstrap is therefore an **external**
same-origin script at `ui/public/theme-init.js` (not inline) so `script-src 'self'` holds.
If you touch theming, keep it there — don't reintroduce an inline `<script>` in
`index.html` (it would be blocked). `style-src` allows `'unsafe-inline'` for the app's
`style={{…}}` attributes.

## Structure (`src/`)

- `api/` — typed API layer: `client.ts` (fetch wrapper), `hooks.ts` (React Query hooks),
  `admin.ts`, `corpus.ts`, `types.ts`. **All server calls go through here** — components don't
  fetch directly. `npm run gen:api` regenerates `schema.gen.ts` from the live OpenAPI spec.
- `app/` — shell + routing: `router.tsx`, `nav.ts` (the nav registry — a screen is `ready:true`
  here when live), `Shell.tsx`, `NavRail.tsx`, `TopBar.tsx`, `ApiKeyGate.tsx`.
- `features/` — screens grouped by audience: `business/` (ask, scenario, factors, corpus),
  `system/` (health, map, stores, traces), `maintenance/` (index, ingestion, quality, curation).
- `components/` — shared UI: `Citations`, `EvidenceRail`, `RouteBadge`, `ConfidenceReadout`
  (faithfulness signal), `SavedViews` / `RecentQueries` (persistence), `JobConsole`, `Async`, …
- `lib/` — non-UI helpers: `sse.ts` (streaming), `shockVocab.ts` (**mirror of
  `src/synthesis/vocab.py`**, plus the hard validation-code set from
  `src/synthesis/validate.py`; keep both in sync), `persist.ts`, `download.ts`,
  `traceStats.ts`. `styles/` — global styles.

## Conventions

- Data fetching = React Query hooks in `api/hooks.ts`; keep server types in `api/types.ts`
  (or generated `schema.gen.ts`) — don't redeclare response shapes in components.
- **Adding a screen:** create `features/<area>/<screen>/`, wire a route in `app/router.tsx`,
  register it in `app/nav.ts` (set `ready` when it's real, not a placeholder), and reach the
  backend only via an `api/` hook.
- The shock-vector table and any factor/vocab UI must match the backend controlled vocab
  (`shockVocab.ts` ↔ `src/synthesis/vocab.py`, 19 fields incl. the `MOVE_*` term structure,
  `EQUITY_US` and the rates decomposition `REAL_YIELD_10Y` / `TERM_PREMIUM_10Y`).
- Verify changes with `npm run build --prefix ui` (type-checks) before considering them done.
