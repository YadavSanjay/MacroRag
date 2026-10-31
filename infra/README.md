# Macro RAG — infrastructure

**What is in `infra/`, and where each thing is documented.** This file is a map of the
artifacts; the operating instructions live in the guides and are not repeated here:

| You want to… | Read |
|---|---|
| Run the stack, monitor it, tune caching, follow the runbook | [guide 08 — Operations & Deployment](../docs/guides/08-operations-and-deployment.md) |
| Build the images, push to ECR, bootstrap and deploy the EC2 | [guide 09 — Packaging & Deployment](../docs/guides/09-packaging-and-deployment.md) |
| Know which AWS resources must exist first | [AWS deployment §4](../docs/design/Macro_RAG_AWS_Deployment.md) |

```
infra/
  docker/   Dockerfile (backend) + docker-compose.yml (UI + API + Qdrant + Redis + OTel)
            + docker-compose.aws.yml (single-EC2 aws deploy_mode: external Qdrant/RagInference, S3, EBS)
  otel/     OpenTelemetry Collector config (exports to otlp/tempo + debug)
  helm/     Helm chart — two workloads (API + UI), optional Ingress
  config/   rag.prod.yaml (server/redis/otel/quantization overrides)
            + rag.aws.yaml (deploy_mode: aws — S3 corpus, external Qdrant/RagInference)
```

The UI image is built from **`ui/Dockerfile`** (not under `infra/`) — see
[ui/CLAUDE.md](../ui/CLAUDE.md) and [UI containerization](../docs/design/UI_Containerization.md).

## Two lightweight images + external inference

The stack is **two purpose-built images** — `macrorag-ui` (nginx SPA, `ui/Dockerfile`)
and `macrorag` (the FastAPI backend, `infra/docker/Dockerfile`) — combined on one host
by compose. Neither carries the multi-GB ML stack:

- The **backend image is lightweight** (~700 MB, no torch). It installs the trimmed
  `requirements-serve.txt` (no `FlagEmbedding` / `sentence-transformers` / `playwright`)
  and delegates embedding + reranking to the sister project **RagInference** over HTTP
  (`RemoteEmbedder` / `RemoteReranker`).
- **RagInference is hosted separately** (its own process/image, owned by that project) —
  it is **not** part of this compose. Point the backend at it with `RAGINFER_URL`
  (compose defaults to `http://host.docker.internal:8799`, i.e. RagInference running on
  the host; set it to an in-cluster service URL for k8s).
- `inference.fallback_local` is forced **off** in `rag.prod.yaml`: the local model deps
  aren't in the image, so an unreachable service must error rather than attempt an
  impossible in-process load. (This value is read from config, not the
  `RAGINFER_FALLBACK_LOCAL` env var — the retriever passes it explicitly.)

So: start RagInference first (sister project), then `docker compose up`.

## Why a Qdrant *server*

Local on-disk Qdrant (`QdrantClient(path=...)`) deadlocks on the bulk upsert of
the full corpus. Production therefore points `qdrant.url` at a Qdrant **server**
(provided by the compose stack). `:memory:` remains fine for tests.

## The UI service (why it is the front door)

The **ui** container (nginx, built from `ui/Dockerfile`) serves the pre-built React SPA and
reverse-proxies `/api/*` to **app**, **stripping the `/api` prefix** so the bundle's relative
`/api` base reaches the root-level backend routes. That trailing-slash `proxy_pass` is what
reproduces the dev Vite proxy exactly — which is why one image runs unchanged in dev,
compose, k8s and AWS. The only deploy-time knob is `API_UPSTREAM`; **no backend URL is baked
into the JavaScript**. SSE (streamed answers, job-log streams) works through it because
buffering is disabled in `ui/nginx/default.conf.template`. The UI needs no secrets and no
`.env` — the API key is entered in-browser and forwarded as `X-API-Key`.

## Running it

Commands are **not duplicated here** — each topology has one documented home:

| Topology | Files | Documented in |
|---|---|---|
| Local compose (ui :8080 · app :8000 · qdrant · redis · otel) | `docker/docker-compose.yml` + `config/rag.prod.yaml` | [guide 08 §2](../docs/guides/08-operations-and-deployment.md) |
| Kubernetes (two workloads + optional Ingress) | `helm/` | [guide 08 §3](../docs/guides/08-operations-and-deployment.md) |
| AWS single-EC2 (`deploy_mode: aws`) | `docker/docker-compose.aws.yml` + `config/rag.aws.yaml` | [guide 08 §2.1](../docs/guides/08-operations-and-deployment.md) (runtime) · [guide 09](../docs/guides/09-packaging-and-deployment.md) (build → push → deploy) |

Two Helm values are easy to miss: **`raginferUrl` is required** (the API image ships no
models), and **`configOverride`** mounts a *complete* `rag.yaml` over the baked one — its
main use is `inference.fallback_local: false`, which cannot be set by env.

In `aws` mode the switch is `deploy_mode: aws` (env `MACRORAG_DEPLOY_MODE`), which routes
corpus storage through the `utils.objectstore.ObjectStore` seam
(`LocalObjectStore` → `S3ObjectStore`); S3 credentials come from the EC2 **instance IAM
role**, never from config. Data-placement matrix and the AWS resources infra must provision:
[AWS deployment](../docs/design/Macro_RAG_AWS_Deployment.md) §4 and §5.

## Observability

The app exports OTLP spans (`rag.query` / `rag.retrieve` / `rag.generate` / `rag.verify`) to
the collector when `observability.otel_enabled` is set; `otel/otel-collector-config.yaml`
exports to `otlp/tempo` and keeps `debug`, so an unreachable Tempo only logs retries rather
than stopping the collector. Per-query JSONL traces (route, chunk scores, timings, tokens)
are written to `data/traces/queries.jsonl` regardless, and the API serves Prometheus
`/metrics`. Details: [guide 08 §4](../docs/guides/08-operations-and-deployment.md).

## CI

CI lives in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) (not under `infra/`):
lint · standards (advisory) · mypy · tests · UI type-check + build · `pip-audit` (advisory) ·
build **both** container images (no push). Job-by-job breakdown:
[guide 08 §8](../docs/guides/08-operations-and-deployment.md). ECR push and deploy are manual —
[guide 09 §5](../docs/guides/09-packaging-and-deployment.md).
