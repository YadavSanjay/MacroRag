# UI containerization

How the React/Vite UI (`ui/`) is packaged as its own service beside the API, with no host-local
dependencies: no host Node path, no baked-in backend URL, no `.env` coupling. One image runs
unchanged on any host, in compose, in CI and on Kubernetes.

Operating instructions — building, pushing, deploying — are in
[guide 09](../guides/09-packaging-and-deployment.md) and [infra/README.md](../../infra/README.md).
The files themselves (`ui/Dockerfile`, `ui/nginx/default.conf.template`,
`infra/docker/docker-compose.yml`, `infra/helm/templates/ui-*.yaml`, root `.dockerignore`) are
authoritative; this doc explains the design.

---

## 1. Why the UI containerizes cleanly

| Concern | Shape | Consequence |
|---|---|---|
| Backend URL | `ui/src/api/client.ts` uses a **relative** `BASE = "/api"` | The bundle is host-agnostic as built; nothing is injected per environment |
| Backend routes | Root-level (`/healthz`, `/query`, `/admin/…`), no `/api` prefix | Whatever serves the UI must strip `/api` before forwarding — in every topology |
| Dev proxy | Vite proxies `/api` → `VITE_API_TARGET` and strips the prefix | Production must reproduce exactly that: serve assets **and** strip `/api` |
| Streaming | SSE over `fetch` (`ui/src/lib/sse.ts`): `POST /api/query/stream`, `GET /api/admin/jobs/{id}/stream` | The proxy must not buffer and must allow long reads |
| Auth | API key typed in the browser, kept in `localStorage`, sent as `X-API-Key` | The UI image holds **no secrets**; `.env` never goes near it |
| Build-time config | `VITE_*` is used only by `vite.config.ts` (the dev proxy) | No runtime `window.__ENV__` shim is needed |

So the whole coupling reduces to "who serves the static files and forwards `/api`" — a
deploy-time concern handled by one nginx variable.

## 2. Design principles

- **One immutable image, many environments.** Differences between environments are nginx
  runtime config, never bundle contents.
- **Static-first.** Pre-built assets on nginx; no Node server in production.
- **Same-origin.** UI and `/api` share one origin: no CORS, no preflight, SSE works through one
  proxy setting. Keep `api.cors_allow_origins` empty in this topology.
- **Least privilege.** Non-root nginx, no secrets in the image.
- **Parity** with the backend image's Dockerfile/compose/Helm conventions.

## 3. Topology

```
                     ┌──────────── ui (nginx-unprivileged, :8080) ─────────────┐
browser ── :8080 ──► │  /        → static dist/ (SPA fallback to index.html)   │
                     │  /api/*   → proxy_pass http://${API_UPSTREAM}/ (strips) │
                     └─────────────────────────────┬───────────────────────────┘
                                                   ▼
                                     app (FastAPI :8000, root-level routes)
                                                   │ HTTP
                                     RagInference (embed + rerank) · Qdrant · Redis
```

nginx serving the build and reverse-proxying `/api` reproduces the dev semantics exactly, so
`client.ts`, `sse.ts` and the hooks need no production variant. `API_UPSTREAM` is the only
deploy-time variable.

**The backend is a separate, lightweight image** (`infra/docker/Dockerfile`, deps from
`requirements-serve.txt`): no torch, no FlagEmbedding / sentence-transformers / Playwright. The
bge-m3 embedder and the reranker are served by the external **RagInference** service, reached
through `RAGINFER_URL`; `inference.fallback_local` is forced off in `infra/config/rag.prod.yaml`
so an unreachable service fails loud instead of attempting an in-process model load. The heavy-ML
imports (`embeddings/bge_m3.py`, `retriever/rerank.py`, `ingest/common/browser.py`) are deferred,
so the modules import without those packages. RagInference is not part of the compose stack —
start it separately.

**Not built, kept as options:** FastAPI serving the SPA itself (one container, but couples a Node
build into the Python image), and a standalone UI on a different origin calling an absolute API
URL (needs a runtime `window.__ENV__` shim, CORS and cross-origin SSE care — only worth it for a
split-origin/CDN deployment).

## 4. Image (`ui/Dockerfile`)

- **Build stage** `node:24.14.0-alpine`: `npm ci` from the lockfile, then `npm run build`
  (`tsc` + `vite build`). The Node pin matches the dev toolchain in `ui/CLAUDE.md`; bump both
  together.
- **Runtime stage** `nginxinc/nginx-unprivileged:1.27-alpine`: runs as uid 101 on port 8080,
  copies `dist/` and the nginx template. The base image's entrypoint runs `envsubst` over
  `/etc/nginx/templates/*.template` at start, so `${API_UPSTREAM}` (default `app:8000`) is
  substituted at run time.
- Built from the **repo root** (`docker build -f ui/Dockerfile .`) for parity with the backend
  image and a shared `.dockerignore` (which excludes `ui/node_modules`, `ui/dist`, `data/`, `.git`).

## 5. nginx (`ui/nginx/default.conf.template`)

- `location /api/ { proxy_pass http://${API_UPSTREAM}/; … }` — the **trailing slash** replaces
  the matched `/api/` prefix with `/`, the production equivalent of Vite's
  `rewrite: p => p.replace(/^\/api/, "")`. This is the line everything depends on.
- Streaming: `proxy_buffering off`, `proxy_cache off`, `proxy_read_timeout` /
  `proxy_send_timeout 3600s`, HTTP/1.1 with an empty `Connection` header. Without
  `proxy_buffering off`, SSE frames arrive in one lump at stream end.
- `/assets/` (hashed files): one-year immutable cache. `/`: SPA fallback to `index.html`.
- Security headers on the app shell: a strict CSP (`script-src 'self'`,
  `frame-ancestors 'none'`, `connect-src 'self'`; `style-src` allows inline styles),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  and a restrictive `Permissions-Policy`.
- **Constraint that matters:** the theme bootstrap lives in `ui/public/theme-init.js`, not
  inline, so `script-src 'self'` holds without per-build hashes. Never add an inline `<script>`
  to `index.html` — the CSP will block it.

## 6. Compose and Kubernetes

- **Compose** (`infra/docker/docker-compose.yml`): service `ui` builds `ui/Dockerfile`, publishes
  `8080:8080` (the front door), sets `API_UPSTREAM=app:8000`, and depends on `app`.
- **Helm:** `ui-deployment.yaml` and `ui-service.yaml` mirror the API templates; `values.yaml`
  has a `ui:` block (image, `replicaCount: 2`, ClusterIP port 8080, requests 50m/64Mi, limits
  250m/128Mi). The optional Ingress fronts **only the UI Service**; `/api` is proxied inside the
  pod, which keeps the image identical between compose and Kubernetes. An Ingress-level `/api`
  route to the API Service is the alternative if the in-pod proxy is ever dropped.
- **CI** builds both images (`docker-build`) and runs the UI type-check + build (`ui-build`).

## 7. Verification checklist

- `docker build -f ui/Dockerfile .` succeeds on a clean checkout with no host Node.
- `/` and deep links (e.g. `/scenario`) load, including on hard refresh.
- `/api/healthz` through the UI container returns the API's health (proves strip + upstream).
- A streamed query renders incrementally, and a job's log stream ticks live in `JobConsole`.
- A bad `X-API-Key` surfaces 401; a good one works (header pass-through).
- The container runs as uid 101 and the final layer holds no secrets, `data/` or `node_modules`.
- The same image works in compose and against a remote API by changing only `API_UPSTREAM`.
