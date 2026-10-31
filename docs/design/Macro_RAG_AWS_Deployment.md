# MacroRAG — AWS Deployment (dual mode: local + AWS)

How one codebase runs two ways — on a developer machine and on AWS — selected by a single
`deploy_mode` switch, and what the infra project must provision for the AWS target.

AWS provisioning (VPC, EC2, EBS, S3, IAM, security groups) belongs to the separate infra project
(`aws-genai-iac`). The **authoritative list of what infra must provision is
[§4](#4-aws-services-to-provision-infra-reference)**; every other section describes application
behaviour. The two repos meet at the ECR repositories: this repo builds, gates and pushes the
images; infra deploys them.

> **Where to go for what.** The step-by-step runbook — image build, acceptance gate, ECR push,
> instance bootstrap, corpus seeding, deploy, rollback, troubleshooting — is
> [guide 09](../guides/09-packaging-and-deployment.md). The runtime shape (what runs where,
> observability, caching) is [guide 08 §2.1](../guides/08-operations-and-deployment.md). Commands
> are not duplicated here.

---

## 1. The two modes

- **`local`** (default) — everything on the local filesystem; no AWS dependency; tests run here.
- **`aws`** — corpus object data on **S3**; derived SQLite stores, traces and eval artifacts on an
  **EBS** volume; **Qdrant** and **RagInference** on their own in-VPC hosts; secrets from
  **Secrets Manager/SSM**; the service layer as **Docker Compose on a single EC2**.

**One code path, config-driven.** No `if aws:` in business logic: the mode only chooses
implementations (an object-store backend, endpoint defaults) at construction time, behind
interfaces. Local mode is byte-identical to a build without the AWS code.

These concerns were already environment-neutral and need only config values on AWS:

| Concern | Externalized via |
|---|---|
| Qdrant endpoint | `qdrant.url` / `MACRORAG_QDRANT_URL` (server mode — the local on-disk mode deadlocks on bulk upsert) |
| Embedding + rerank | `RAGINFER_URL` / `inference.remote_url` — retrieval **and** indexing embed through it via `embeddings.select_embedder` |
| Redis | `redis.url` / `MACRORAG_REDIS_URL` (`rediss://` works for ElastiCache TLS) |
| OTel | `MACRORAG_OTEL_ENDPOINT` |
| Secrets | `read_secret` / `get_secret` with the `<NAME>_FILE` convention |
| No-torch service image | `requirements-serve.txt` + `inference.fallback_local: false` |
| Health | `GET /healthz` (ALB target group + compose healthcheck) |
| UI | relative `/api` + `API_UPSTREAM` — same image everywhere |

## 2. Topology

```
                       ┌────────────────────────── VPC ──────────────────────────┐
  Internet ── ALB ──►  │  EC2 "service"  (single instance, Docker Compose)        │
   (443, UI only)      │   ├─ ui     (nginx :8080) — front door, /api proxy        │
                       │   ├─ app    (uvicorn :8000, no torch)                     │
                       │   ├─ redis  (:6379, cache)                                │
                       │   └─ otel-collector (:4317)                               │
                       │        │  EBS at /mnt/macrorag-data → /app/data           │
                       │        │   vectorstore/timeseries.sqlite, cache/, traces/,│
                       │        │   eval/                                          │
                       │        ├──► Qdrant server    (separate host, :6333, TLS)  │
                       │        ├──► RagInference     (separate host, :8799)       │
                       │        └──► S3 (corpus: raw/ processed/ manifest)         │
                       │  IAM instance role: S3 corpus bucket + secrets + ECR pull │
                       └──────────────────────────────────────────────────────────┘
   OpenAI / Anthropic ◄── egress (NAT)        Secrets Manager / SSM ◄── keys at boot
```

- **Only the UI is public** (ALB → :8080). API, Qdrant and RagInference stay private; the UI
  proxies `/api` same-origin.
- **A single service EC2** makes in-process jobs, single-writer SQLite and per-process state all
  correct, which keeps the design simple.

## 3. Design decisions

1. **`deploy_mode`** (`MACRORAG_DEPLOY_MODE`) selects the object-store backend
   (`storage.backend` follows it unless set explicitly) and supplies AWS-flavoured defaults.
   Nothing else branches on it.
2. **S3 for corpus object data only** (raw, processed, manifest + manifest history). These are
   object-shaped, list/get/put, and the durable corpus artifact worth keeping independent of the
   instance. They go through `utils.objectstore.ObjectStore` — `LocalObjectStore`,
   `S3ObjectStore` (lazy `boto3`, injectable client), `InMemoryObjectStore` for tests — wired into
   `RawStore`/`DocumentStore`, `corpus.iter_documents`, the manifest, the index pipeline, the
   corpus/admin API read paths and the curation jobs.
3. **SQLite stays on EBS, not RDS.** Both SQLite stores are single-writer and **rebuildable**
   (`timeseries.sqlite` from the S3 corpus via `index`; `ingest_cache.sqlite` is a disposable
   HTTP cache). RDS would solve a multi-writer problem this deployment doesn't have, at the cost
   of a Postgres port, network hops per query, and lost local/prod parity. EBS snapshots are the
   durability. `TimeseriesStore` stays behind its class boundary so a Postgres implementation is
   a clean swap if the service tier ever scales out.
4. **Traces, feedback and eval artifacts stay on EBS** — append-pattern logs that S3 handles
   poorly. They can be rotated to S3 or shipped to CloudWatch later.
5. **Qdrant and RagInference are external, reached by URL.** Qdrant runs TLS with a self-signed
   certificate on a private, security-group-locked network (`https: true`, `verify: false`);
   API-key auth is supported (`qdrant.api_key_env`) but not yet enabled on the shared host.
   RagInference is plaintext inside the VPC.
6. **Redis is a local compose container**, not ElastiCache: the cache is disposable and there is
   one instance. Switch `MACRORAG_REDIS_URL` to an ElastiCache `rediss://` endpoint only for
   cross-instance or durable caching.
7. **Secrets are rendered to an env file at boot** from Secrets Manager (simplest with compose);
   `_FILE` mounts work equally without code change.

## 4. AWS services to provision (infra reference)

The application needs **no AWS resource in local mode**. "Required" = needed for a working AWS
deployment; "Optional" = recommended hardening the app runs without. **App wiring** names the env
var or config the application reads (§6), or the IAM action it relies on.

### 4.1 Compute

| Service | Req. | Purpose / spec | App wiring |
|---|---|---|---|
| **EC2 — service tier** | ✅ | One instance running Docker + Compose (UI + API + Redis + OTel). CPU-only, **no GPU, no torch** — ≈2–4 vCPU / 8–16 GB (e.g. `t3.large`/`xlarge`). Private subnet. | runs `docker-compose.aws.yml`; EBS at `/mnt/macrorag-data` → container `/app/data` |
| **EC2 — Qdrant server** | ✅ | Separate instance, Qdrant `:6333` (server mode mandatory). Memory sized to the corpus (int8 quantization on); its **own EBS** for the index. Private subnet. | `MACRORAG_QDRANT_URL=https://<host>:6333` |
| **EC2 — RagInference** | ✅ | Separate instance, shared embedder + reranker `:8799` (owned by the sister project, must live in this VPC). GPU (e.g. `g5.xlarge`) or CPU; **its own EBS for `HF_HOME`/weights**. Private subnet. | `RAGINFER_URL=http://<host>:8799` |

### 4.2 Storage

| Service | Req. | Purpose / spec | App wiring |
|---|---|---|---|
| **S3 bucket — corpus** | ✅ | raw / processed / manifest objects. Enable **versioning** + SSE (SSE-S3 or KMS); **block public access**. Layout: `{prefix}/raw/ingest/…`, `/processed/ingest/…`, `/vectorstore/manifest.json` (+ `manifest_history.jsonl`). | `MACRORAG_S3_BUCKET`, `MACRORAG_S3_PREFIX`, `MACRORAG_S3_REGION` |
| **EBS — service tier** | ✅ | `timeseries.sqlite`, ingest cache, traces, eval. gp3 ≈20–50 GB. **Enable snapshots** (§3 item 3). | mounted at `/mnt/macrorag-data` |
| **EBS — Qdrant / RagInference** | ✅ | One volume each (vector index; model weights). | host-local |
| RDS / EFS | ❌ | Not used (§3 item 3). | — |

### 4.3 Networking

| Service | Req. | Purpose | Notes |
|---|---|---|---|
| **VPC + subnets** | ✅ | Public subnet(s) for the ALB; private subnet(s) for service / Qdrant / RagInference. | all app traffic private except the ALB |
| **NAT gateway** | ✅ | Egress from the service EC2 to OpenAI/Anthropic, the macro data-source sites (ingestion), and HuggingFace (RagInference host). | private subnets → internet |
| **Application Load Balancer** | ✅ | Public HTTPS `:443` → UI target `:8080`; only the UI is exposed. | raise the **idle timeout** for SSE (`/query/stream`, job-log streams) |
| **ACM certificate** | ✅ | TLS for the ALB domain. | on the `:443` listener |
| **Security groups** | ✅ | Per §4.5; isolation is the primary control. | least privilege, source-SG-scoped |
| **VPC endpoints** | ⬜ | S3 **gateway** endpoint (recommended); `ecr.api` + `ecr.dkr` interface endpoints; Secrets Manager interface endpoint. ECR endpoints still need the S3 gateway (layers are served from S3). | required if the instance has no NAT |
| **Route 53** | ⬜ | Public DNS for the ALB; a private zone (or Cloud Map) for the Qdrant / RagInference names. | otherwise raw private DNS/IPs in env |

### 4.4 Identity, secrets, registry, ops

| Service | Req. | Purpose | App wiring |
|---|---|---|---|
| **IAM instance role (service EC2)** | ✅ | `s3:GetObject/PutObject/ListBucket/DeleteObject` on the corpus bucket; `secretsmanager:GetSecretValue` (or `ssm:GetParameter`); CloudWatch Logs write; **ECR pull** (`ecr:GetAuthorizationToken` on `*`, plus `BatchGetImage` / `GetDownloadUrlForLayer` / `BatchCheckLayerAvailability` on the two repos). | boto3 takes S3 credentials from the role — **no keys on disk** |
| **Secrets Manager** (or SSM Parameter Store) | ✅ | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, source keys (FRED/BEA/BLS/EIA/Census/…), optional `QDRANT_API_KEY`. Payload as **`KEY=value` lines** (compose `env_file` format). | rendered at boot, or mounted as `<NAME>_FILE` |
| **ECR** | ✅ | Two repos (backend and UI). Images are pushed by `scripts/build_push_images.py` (a push identity with `ecr:PutImage` / `UploadLayerPart`); the instance only pulls. Images must match the instance architecture (amd64 or Graviton arm64). | `MACRORAG_IMAGE` / `MACRORAG_UI_IMAGE` |
| **CloudWatch** / trace backend | ⬜ | Logs and metrics; or OTel collector → ADOT (X-Ray/AMP) or Tempo. | `MACRORAG_OTEL_ENDPOINT`; Prometheus `/metrics` on the API |
| **ElastiCache (Redis)** | ⬜ | Only for cross-instance or durable cache (§3 item 6). | `MACRORAG_REDIS_URL=rediss://…` |

### 4.5 Ports (for security-group rules)

| From → To | Port | Purpose |
|---|---|---|
| Internet → ALB | 443 | UI over HTTPS (the only public ingress) |
| ALB → service EC2 | 8080 | UI nginx (SPA + `/api` proxy) |
| service EC2 → Qdrant host | 6333 | vector store (6334 if gRPC) |
| service EC2 → RagInference host | 8799 | embed + rerank |
| service EC2 → S3 / Secrets Manager | 443 | corpus objects, boot secret fetch |
| service EC2 → NAT → internet | 443 | LLM providers, data-source sites |
| within service EC2 | 6379 / 4317 | Redis, OTel (compose-internal) |

### 4.6 Not required

Kubernetes/EKS, ECS, RDS, EFS and an API-facing load balancer are not needed for the single-EC2
target; they matter only if the service tier scales past one instance (§9).

## 5. Data placement

| Artifact | `local` | `aws` | Seam |
|---|---|---|---|
| raw bytes | `data/raw/ingest/` | S3 `raw/` | `RawStore` → ObjectStore |
| processed docs | `data/processed/ingest/` | S3 `processed/` | `DocumentStore` / `iter_documents` → ObjectStore |
| manifest + history | `data/vectorstore/` | S3 `vectorstore/` | `utils.manifest` → ObjectStore |
| `timeseries.sqlite` | `data/vectorstore/` | EBS `/app/data/vectorstore/` | filesystem |
| `ingest_cache.sqlite` | `data/cache/` | EBS `/app/data/cache/` | filesystem |
| Qdrant index | Qdrant container (named volume) | separate Qdrant host | `qdrant.url` |
| traces / feedback / eval | `data/traces/`, `data/eval/` | EBS `/app/data/…` | filesystem |
| model weights | RagInference host | RagInference host | outside this repo |
| Redis | optional local container | compose container (or ElastiCache) | `redis.url` |
| secrets | `.env` | Secrets Manager / SSM | `read_secret` / `get_secret` |

## 6. Configuration surface

`infra/config/rag.aws.yaml` is the AWS override file (mounted by `docker-compose.aws.yml`). Its
AWS-specific keys:

```yaml
deploy_mode: aws
storage:
  backend: s3
  s3_bucket: <corpus-bucket>          # or MACRORAG_S3_BUCKET
  s3_prefix: corpus
qdrant:
  url: https://<qdrant-host>:6333     # or MACRORAG_QDRANT_URL
  https: true
  verify: false                       # self-signed cert on a private network
  # api_key_env: QDRANT_API_KEY       # when Qdrant auth is enabled
inference:
  remote_url: http://<raginference-host>:8799
  fallback_local: false               # no torch in the service image
```

It also carries the tuned retrieval, synthesis, cache and LLM-tier values so the AWS stack serves
like the local one. Env overrides (win over YAML): `MACRORAG_DEPLOY_MODE`, `MACRORAG_S3_BUCKET`,
`MACRORAG_S3_PREFIX`, `MACRORAG_S3_REGION`, `MACRORAG_QDRANT_URL`, `RAGINFER_URL`,
`MACRORAG_REDIS_URL`, `MACRORAG_OTEL_ENDPOINT`, `OPENAI_API_KEY[_FILE]`,
`ANTHROPIC_API_KEY[_FILE]`. SQLite and traces stay under `/app/data` (the EBS mount) through the
default `paths.*`.

**Secrets.** Every secret resolves through `read_secret` (RAG layer) or `get_secret` (ingest),
both preferring `<NAME>_FILE` over the plain variable. S3 needs no application secret (instance
role). The service EC2 never loads models, so it needs no HuggingFace token.

## 7. Corpus and indexing on AWS

- **Seed from the curated local corpus.** A fresh ingest on the instance cannot reproduce it:
  FRED serves the ICE BofA spreads only over a rolling window, and some documents that left their
  feeds were recovered by hand. `scripts/sync_corpus.py` copies the local processed corpus to the
  bucket (dry run by default; `--apply` copies, never deletes; `--overwrite` replaces differing
  objects). Then run a full `index` on the instance. The resulting `world_view_id` matches the
  local one, because it depends only on the `(chunk_id, content_hash)` pairs.
- **On-demand ingest and index** work as locally — CLI or `POST /admin/ingest` /
  `POST /admin/index` as in-process jobs. Ingest writes raw/processed to S3; index reads the
  corpus from S3, embeds through RagInference (failing loud if it is down), upserts to the
  external Qdrant, builds `timeseries.sqlite` on EBS and writes the manifest to S3.
- The `corpus` exclusive-group lock serializes store-mutating jobs. With one instance,
  `jobs.backend: memory` is sufficient.

## 8. Observability, testing and parity

- OTLP spans go to the compose `otel-collector` sidecar, exported to the chosen backend; the
  Prometheus `/metrics` endpoint stays on the API; per-query JSONL traces stay on EBS.
- Tests stay torch- and network-free (`InMemoryObjectStore`, injected-client `S3ObjectStore`);
  `test/test_objectstore.py` covers the store contract and the corpus/manifest round-trips.
- AWS mode can be verified without AWS resources (guide 09 §3.2). The on-instance smoke test is:
  seed and index → objects in S3, Qdrant populated, `timeseries.sqlite` on EBS → a grounded
  `/query` answer, and `/healthz` reporting a non-null `index`.

## 9. Not built / limits

- **CI does not push images or deploy.** CI builds both images without pushing; the push runs
  through `scripts/build_push_images.py`, and the deploy (`compose pull && up -d`, e.g. via SSM
  run-command) is manual.
- **RagInference auth** (bearer token / TLS on the inference clients) — plaintext in the VPC.
- **Qdrant API-key auth** — supported in config, not enabled on the shared host.
- **Scaling past one service instance** would need: SQLite → Postgres for the time-series store,
  `jobs.backend: redis`, and traces shipped to S3/CloudWatch.
