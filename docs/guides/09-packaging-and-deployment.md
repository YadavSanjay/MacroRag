# 09 — Packaging & Deployment (local images → AWS ECR → EC2)

How to build the two container images, verify them locally, publish them to **AWS ECR**, and
run them on the **single EC2** service host in `deploy_mode: aws`.

> Audience: operators / release engineers. Runtime operations (monitoring, caching, runbook)
> are in [08 — Operations & Deployment](08-operations-and-deployment.md); the AWS resources
> that must exist first are listed in
> [AWS deployment §4](../design/Macro_RAG_AWS_Deployment.md).

---

## 1. What gets built

Two purpose-built images. **Neither carries the ML stack** — embedding + reranking are
delegated to the external **RagInference** service, so the service host needs no GPU and no torch.

| Image | Built from | Base | Size | Port | Contents |
|---|---|---|---|---|---|
| `macrorag` (backend) | `infra/docker/Dockerfile` | `python:3.12-slim` | ~750 MB | 8000 | `requirements-serve.txt` (no torch), `src/`, `prompts/`, `configs/`; uvicorn `api.server:app` |
| `macrorag-ui` | `ui/Dockerfile` | `nginx-unprivileged:1.27-alpine` (multi-stage from `node:24.14.0-alpine`) | ~75 MB | 8080 | built SPA + nginx `/api` reverse proxy |

Both build from the **repo root** as context; `.dockerignore` keeps `data/`, secrets, `.git`,
`docs/`, `test/`, and `ui/node_modules` out of the context and the layers.

The backend image includes `boto3`, so the same image serves both `deploy_mode: local` and
`aws` — the mode is chosen at run time by env, not at build time.

---

## 2. Prerequisites

| Need | Notes |
|---|---|
| Docker (with Compose v2) | `docker --version`, `docker compose version` |
| Repo checkout | builds use the repo root as context |
| For ECR | AWS CLI v2 + credentials with ECR push rights; the two ECR repos created |
| **Architecture match** | Images built on an x86 machine are `linux/amd64`. If the EC2 instance is **Graviton (arm64)** you must cross-build — see §5.4 |

---

## 3. Build and verify locally

```bash
# from the repo root
docker build -f infra/docker/Dockerfile -t macrorag:latest .
docker build -f ui/Dockerfile            -t macrorag-ui:latest .
```

### 3.1 Smoke-test the images

Verify the pair on a throwaway network — this exercises the real front-door path
(browser → UI nginx → `/api` proxy → backend):

```bash
docker network create mrnet
docker run -d --name mr-api --network mrnet macrorag:latest
docker run -d --name mr-ui  --network mrnet -p 18080:8080 \
    -e API_UPSTREAM=mr-api:8000 macrorag-ui:latest

curl -s http://localhost:18080/            -o /dev/null -w "UI  HTTP %{http_code}\n"
curl -s http://localhost:18080/api/healthz                        # -> proxied to the backend
docker ps --filter name=mr-api --format "{{.Status}}"             # -> "Up ... (healthy)"

# cleanup
docker rm -f mr-api mr-ui && docker network rm mrnet
```

Expected:

```
UI  HTTP 200
{"status":"ok","built":false,"index":null}
Up 59 seconds (healthy)
```

`"built": false` is correct on a fresh container — the pipeline builds lazily on first query,
and `"index": null` simply means no manifest exists yet (nothing indexed).
The backend `HEALTHCHECK` (`curl /healthz`) flips to `healthy` within ~30–60 s.

### 3.2 Verify AWS mode without any AWS resources

Confirms the image can actually run in `aws` mode (boto3 present, S3 keys resolve) before you
provision anything:

```bash
docker run --rm -e MACRORAG_DEPLOY_MODE=aws -e MACRORAG_S3_BUCKET=demo-bucket \
  macrorag:latest python -c "
from utils.config import load_settings
from utils.objectstore import corpus_store, manifest_store
s = load_settings()
print(s.deploy_mode, s.storage_backend, corpus_store(s).location)
print(manifest_store(s)[0].location)"
```

```
aws s3 s3://demo-bucket/corpus/processed/ingest
s3://demo-bucket/corpus/vectorstore
```

### 3.3 Run the full local stack

```bash
docker compose -f infra/docker/docker-compose.yml up --build     # UI :8080, API :8000, Qdrant, Redis, OTel
```

Start **RagInference** (sister project) first — the backend delegates embedding/rerank to it.

---

## 4. Environment & dependency reference

Copy the template and fill it in — it is the single inventory of every variable and external
dependency the system uses:

```bash
cp .env.example .env
```

`.env` is gitignored and excluded from the build context; **no secret is ever baked into an
image**. Categories (see the template for the full annotated list):

| Group | Examples | Notes |
|---|---|---|
| Deploy mode / storage | `MACRORAG_DEPLOY_MODE`, `MACRORAG_S3_BUCKET`/`_PREFIX`/`_REGION` | S3 **credentials are not set here** — boto3 uses the EC2 instance role |
| External services | `MACRORAG_QDRANT_URL`, `RAGINFER_URL`, `MACRORAG_REDIS_URL`, `MACRORAG_OTEL_ENDPOINT` | endpoints only |
| LLM secrets | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | also `<NAME>_FILE` for mounted secrets |
| Data-source keys | `FRED_API_KEY`, `BEA_API_KEY`, `BLS_API_KEY`, `EIA_API_KEY`, `CENSUS_API_KEY`, `PJM_API_KEY`, `FINRA_*`, `IEA_*`, `USITC_*` | only for the tiers you ingest; a missing key just disables that source |
| UI | `API_UPSTREAM` | the only UI knob; no backend URL is baked into the JS |

Any secret may instead be supplied as `<NAME>_FILE` pointing at a mounted file (the file wins
over the plain variable) — used for Secrets Manager / K8s secret mounts.

---

## 5. Publish to AWS ECR

### 5.1 Create the repositories (once)

```bash
aws ecr create-repository --repository-name macrorag    --region <region>
aws ecr create-repository --repository-name macrorag-ui --region <region>
```

### 5.2 Authenticate Docker to ECR

```bash
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
```

### 5.3 Tag and push

Tag with an immutable version (a git SHA or release tag) — **avoid deploying `latest`**, which
makes rollbacks ambiguous:

```bash
REG=<acct>.dkr.ecr.<region>.amazonaws.com
TAG=$(git rev-parse --short HEAD)

docker tag macrorag:latest    $REG/macrorag:$TAG
docker tag macrorag-ui:latest $REG/macrorag-ui:$TAG
docker push $REG/macrorag:$TAG
docker push $REG/macrorag-ui:$TAG
```

### 5.4 Cross-building for Graviton (arm64)

Images built on an x86 host are `linux/amd64` and **will not run** on a Graviton instance.
Build for the target platform explicitly:

```bash
docker buildx build --platform linux/arm64 \
  -f infra/docker/Dockerfile -t $REG/macrorag:$TAG --push .
docker buildx build --platform linux/arm64 \
  -f ui/Dockerfile -t $REG/macrorag-ui:$TAG --push .
```

### 5.5 One command: build → gate → push (`scripts/build_push_images.py`)

`scripts/build_push_images.py` wraps §5.3–5.4 as a single parameterized step and adds an
**in-image acceptance gate** between build and push. It builds both images for the target
platform (default `linux/arm64`), SHA-tags them `<registry>/genai-dev/<repo>:<arch>-<sha>`
(`macrorag` + `macrorag-ui`), resolves the ECR registry from STS on push, and pushes only if
the gate passes.

The gate runs **inside the built backend image** — it loads the typed config in `aws` mode
and resolves the dual-mode storage façade to S3 (the same pre-provision smoke as §3.2), so it
catches a missing dependency, an import error, an invalid config, or broken S3 wiring before
anything ships. It needs **no network, no Qdrant, no RagInference, no LLM, and no mounted
config** (a live retrieval gate is a documented follow-on). It runs on the `backend` image
only; the `macrorag-ui` image is static nginx.

```bash
python scripts/build_push_images.py --dry-run     # print the plan, touch nothing
python scripts/build_push_images.py --no-push     # build + gate locally, no push
AWS_PROFILE=genai-tf python scripts/build_push_images.py --tag arm64-<sha>   # build → gate → push
```

Flags: `--platform` / `--namespace` (default `genai-dev`) / `--registry` / `--tag` /
`--region` / `--profile` / `--component` (repeatable) / `--no-gate` / `--no-push` / `--dry-run`.

> **App vs. infra boundary.** This app repo **builds, gates, and pushes** the images; the infra
> repo (`aws-genai-iac`) **deploys** them — its `scripts/deploy_macrorag_bundle.py` uploads the
> compose bundle + `deploy.env` (the pinned image tags) to the corpus bucket, and the EC2
> bootstrap pulls what this script pushed. The two repos meet at the `genai-dev/macrorag*` ECR
> repositories: infra provisions them, this script pushes to them.

---

## 6. Deploy on the EC2 service host

Assumes the AWS resources in [AWS deployment §4](../design/Macro_RAG_AWS_Deployment.md) exist: the
instance with its **IAM role**, the **EBS** volume, the **S3** corpus bucket, and the Qdrant +
RagInference hosts reachable in-VPC.

### 6.1 Deployment variables (everything infra supplies)

All environment-specific values in one place — set these once per host, then every command
below is copy-paste. Persist them in `/opt/macrorag/deploy.env` and `source` it from your
deploy script / systemd unit so a reboot reuses the same pinned tags.

```bash
# ── Registry (ECR) ──────────────────────────────────────────────────────────
export AWS_ACCOUNT_ID=<acct>                  # 12-digit AWS account id
export AWS_REGION=<region>                    # e.g. us-east-1
export ECR_REGISTRY=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com
export BACKEND_REPO=macrorag                  # ECR repository name
export UI_REPO=macrorag-ui
export IMAGE_TAG=<git-sha-or-release-tag>     # pin a real tag; avoid "latest"

# Consumed by docker-compose.aws.yml
export MACRORAG_IMAGE=${ECR_REGISTRY}/${BACKEND_REPO}:${IMAGE_TAG}
export MACRORAG_UI_IMAGE=${ECR_REGISTRY}/${UI_REPO}:${IMAGE_TAG}

# ── Secrets file (rendered from Secrets Manager at boot, §6.1) ──────────────
export MACRORAG_ENV_FILE=/opt/macrorag/app-secrets

# ── Object storage (S3 corpus) ─────────────────────────────────────────────
export MACRORAG_S3_BUCKET=<corpus-bucket>
export MACRORAG_S3_PREFIX=corpus
export MACRORAG_S3_REGION=${AWS_REGION}

# ── In-VPC service endpoints ───────────────────────────────────────────────
export MACRORAG_QDRANT_URL=http://<qdrant-host>:6333
export RAGINFER_URL=http://<raginference-host>:8799
```

### 6.2 One-time instance bootstrap

```bash
# 1. Docker + compose plugin (Amazon Linux 2023)
sudo dnf install -y docker && sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user           # re-login for group to take effect

# 2. Mount the EBS data volume (format ONLY on first use — this erases the device)
sudo mkfs -t xfs /dev/nvme1n1              # first use only
sudo mkdir -p /mnt/macrorag-data
echo '/dev/nvme1n1 /mnt/macrorag-data xfs defaults,nofail 0 2' | sudo tee -a /etc/fstab
sudo mount -a

# 3. Render secrets from Secrets Manager -> the compose env file
sudo mkdir -p /opt/macrorag
aws secretsmanager get-secret-value --secret-id macrorag/app --query SecretString --output text \
  | sudo tee /opt/macrorag/app-secrets >/dev/null
sudo chmod 600 /opt/macrorag/app-secrets
```

> The secret's payload must be **`KEY=value` lines** (compose `env_file` format), not JSON. If
> you store JSON, convert it — e.g. pipe through
> `jq -r 'to_entries[] | "\(.key)=\(.value)"'`.

### 6.3 Enable ECR image pulls on the instance

The instance must be able to **pull** from ECR. Three things have to be in place — IAM
permissions, a Docker credential source, and network reachability.

**(a) IAM — attach these actions to the instance role** (infra provisions this; see
[AWS deployment §4.4](../design/Macro_RAG_AWS_Deployment.md)). `GetAuthorizationToken` cannot be
resource-scoped; the rest scope to the two repositories:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    { "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage"
      ],
      "Resource": [
        "arn:aws:ecr:<region>:<acct>:repository/macrorag",
        "arn:aws:ecr:<region>:<acct>:repository/macrorag-ui"
      ] }
  ]
}
```

**(b) Credentials — prefer the ECR credential helper over a manual login.** A
`docker login` token **expires after 12 hours**, so a manual login breaks the next reboot or
`compose pull`. The helper re-authenticates automatically from the instance role:

```bash
sudo dnf install -y amazon-ecr-credential-helper        # Amazon Linux 2023
mkdir -p ~/.docker
echo '{"credsStore":"ecr-login"}' > ~/.docker/config.json
```

<details>
<summary>Manual login alternative (expires in 12 h — fine for a one-off)</summary>

```bash
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
```
</details>

**(c) Network — the instance is in a private subnet**, so pulls need either the NAT gateway,
or (cheaper, recommended) these VPC endpoints. Note ECR **layers are served from S3**, so the
S3 gateway endpoint is required even when the two ECR interface endpoints exist:

| Endpoint | Type | Why |
|---|---|---|
| `com.amazonaws.<region>.ecr.api` | Interface | ECR control plane (auth, manifests) |
| `com.amazonaws.<region>.ecr.dkr` | Interface | Docker registry protocol |
| `com.amazonaws.<region>.s3` | **Gateway** | image **layer** download |

Verify pulls work before deploying:

```bash
aws ecr describe-repositories --region <region> --query 'repositories[].repositoryName'
docker pull <acct>.dkr.ecr.<region>.amazonaws.com/macrorag:<tag>
```

### 6.4 Pull the images from ECR

With §6.1 variables exported and §6.3 in place:

```bash
# authenticate (SKIP if the ECR credential helper is installed — §6.3b)
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ECR_REGISTRY"

# pull both images
docker pull "$MACRORAG_IMAGE"
docker pull "$MACRORAG_UI_IMAGE"
```

Or let compose pull exactly what the stack references (equivalent, and guarantees the tags
match what will run):

```bash
docker compose -f infra/docker/docker-compose.aws.yml pull
```

Verify:

```bash
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep macrorag
```

### 6.5 Start the stack

```bash
docker compose -f infra/docker/docker-compose.aws.yml up -d
```

Compose **fails fast** if `MACRORAG_S3_BUCKET` is unset or the secrets file is missing —
deliberate, so a keyless/mis-wired service never starts.

### 6.6 Verify the deployment

```bash
curl -s localhost:8080/api/healthz                 # through the UI proxy (the ALB target)
docker compose -f infra/docker/docker-compose.aws.yml ps
docker compose -f infra/docker/docker-compose.aws.yml logs -f app
```

Then populate the corpus. **Prefer seeding it from the curated local corpus.** A fresh ingest on
the instance cannot reproduce it: FRED serves the ICE OAS pair only over a rolling window
([02 §4](02-data-ingestion.md)), and FOMC minutes that left the feed were recovered by hand. From
the dev machine (AWS credentials from the ambient chain), copy the processed documents to the
bucket, then build the index on the instance:

```bash
python scripts/sync_corpus.py --bucket <corpus-bucket>            # dry run: counts only
python scripts/sync_corpus.py --bucket <corpus-bucket> --apply    # never deletes; --overwrite replaces differing objects
docker compose -f infra/docker/docker-compose.aws.yml exec app python -m pipelines.cli index
```

The full `index` rebuilds the vectors, the time-series store and the manifest from the copied
documents. The `world_view_id` digest then matches the local one, because it depends only on the
`(chunk_id, content_hash)` pairs.

Or ingest from scratch (on demand, from the CLI or the UI admin surface):

```bash
docker compose -f infra/docker/docker-compose.aws.yml exec app python -m ingest --tier tier1_fed
docker compose -f infra/docker/docker-compose.aws.yml exec app python -m pipelines.cli index
```

Confirm the dual-mode wiring actually landed:
- objects appear under `s3://<bucket>/corpus/processed/ingest/` and `…/vectorstore/manifest.json`
- `timeseries.sqlite` exists under `/mnt/macrorag-data/vectorstore/`
- `/api/healthz` reports a non-null `index`

### 6.7 Update / rollback

```bash
export MACRORAG_IMAGE=$REG/macrorag:<new-or-previous-tag>
export MACRORAG_UI_IMAGE=$REG/macrorag-ui:<new-or-previous-tag>
docker compose -f infra/docker/docker-compose.aws.yml pull
docker compose -f infra/docker/docker-compose.aws.yml up -d      # recreates changed services only
```

Because images are pinned by tag and all state lives outside the containers (S3 + EBS + Qdrant),
rollback is just re-deploying the previous tag. **Do not** run `docker compose down -v` — that
would drop the Redis volume (harmless cache) but the command is easy to over-apply.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `required variable MACRORAG_S3_BUCKET is missing a value` | compose guard tripped | export the bucket (§6.2) |
| `env file … not found` | secrets not rendered before `up` | run the bootstrap step §6.2 (3) |
| `exec format error` on start | amd64 image on a Graviton host | rebuild for `linux/arm64` (§5.4) |
| `pull access denied` / `no basic auth credentials` | instance role lacks ECR actions, or no credential source | attach the ECR policy + install the credential helper (§6.3 a/b) |
| ECR pull hangs then times out | private subnet with no NAT and missing endpoints | add `ecr.api` + `ecr.dkr` interface **and** the S3 **gateway** endpoint (§6.3 c) |
| Pull worked yesterday, fails after reboot | manual `docker login` token expired (12 h) | switch to the ECR credential helper (§6.3 b) |
| `denied: requested access to the resource is denied` on **push** | pushing with pull-only rights | pushes come from CI, not the instance — use a CI role with `ecr:PutImage`/`UploadLayerPart` |
| Container `unhealthy` | `/healthz` unreachable | `docker logs`; check the app started and port 8000 is bound |
| UI 200 but `/api/*` 502 | `API_UPSTREAM` wrong or backend down | verify `API_UPSTREAM=app:8000` and that `app` is healthy |
| Retrieval errors mentioning torch / `ImportError` | RagInference unreachable and `fallback_local` on | fix `RAGINFER_URL`; keep `inference.fallback_local: false` (no torch in the image) |
| `NoCredentialsError` / S3 access denied | instance role missing or not attached | attach the IAM role with the S3 actions ([AWS deployment §4.4](../design/Macro_RAG_AWS_Deployment.md)) |
| Qdrant "already accessed by another instance" | embedded on-disk mode in a server deploy | set `MACRORAG_QDRANT_URL` to the Qdrant server |
| SSE / streamed answers truncate | ALB idle timeout too low | raise it; nginx already disables proxy buffering |
| Build context huge / slow | `.dockerignore` bypassed (wrong context dir) | build from the **repo root**, not `infra/docker/` |

---

## 8. CI and what stays manual

`.github/workflows/ci.yml` runs lint, **mypy**, tests, the UI build, the advisory standards and
`pip-audit` jobs, and **builds both images without pushing** (full job table:
[08 §8](08-operations-and-deployment.md)).

Pushing and deploying are **manual**: `scripts/build_push_images.py` (§5.5) builds, gates and
pushes from a workstation, and the deploy is `compose pull && up -d` on the instance (§6.7),
optionally via SSM run-command. CI does not push or deploy
([AWS deployment §9](../design/Macro_RAG_AWS_Deployment.md#9-not-built--limits)).
