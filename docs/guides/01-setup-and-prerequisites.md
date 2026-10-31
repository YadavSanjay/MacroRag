# 01 — Setup & Prerequisites

This guide gets a fresh machine to the point where you can ingest, index, query and
serve MacroRAG. It also explains every external dependency the system relies on.

> Audience: technical operators / developers.
> Prereq reading: [Guides index](README.md).

---

## 1. System prerequisites

| Component | Version / notes |
|---|---|
| **Python** | 3.12 (the backend uses 3.12 type-hint syntax) |
| **pip** | Recent; a virtualenv / conda env is strongly recommended (`py312`) |
| **Node.js** | Only for the web UI. Node 24.x / npm 11 on `PATH` (the UI image pins `node:24.14.0`) |
| **Disk** | Several GB: model weights (~1.4 GB), raw + processed corpus, Qdrant index |
| **GPU (optional)** | CUDA accelerates the bge-m3 embedder and reranker; CPU works but is slower. Auto-detected |
| **Qdrant** | A Qdrant server (Docker, `:6333`) — `configs/rag.yaml` points at it. The embedded on-disk mode suits only small local runs |

---

## 2. Install the backend

From the repo root:

```bash
# (recommended) create and activate an isolated env first, e.g.
#   conda create -n py312 python=3.12 && conda activate py312

pip install -r requirements.txt
```

This installs the full stack: HTTP/parsing (httpx, beautifulsoup4, pdfplumber,
openpyxl, playwright…), embeddings/rerank (FlagEmbedding, sentence-transformers),
the vector store client (qdrant-client), LLM clients (openai, anthropic), the API
(fastapi, uvicorn) and numerics (numpy, pandas).

### Playwright browser (only if you ingest JS-rendered sources)

Some Tier 5 / Tier 6 discovery pages are rendered client-side. They fall back to a
headless Chromium via Playwright. Install the browser binary once:

```bash
playwright install chromium
```

If the binary is missing, those endpoints log a warning and skip — they do not crash
the run.

### What about `PYTHONPATH`?

The CLIs (`python -m ingest`, `python -m pipelines.cli`) are meant to be run from the
repo root. If you see `ModuleNotFoundError: No module named 'utils'` (or `api`,
`pipelines`, …), the `src/` directory isn't on the import path. Fix:

```bash
# POSIX shell
export PYTHONPATH=src
# PowerShell
$env:PYTHONPATH = "src"
```

The Docker image and uvicorn invocations set `PYTHONPATH=/app/src` for you.

---

## 3. Environment variables & secrets (`.env`)

Secrets are loaded from a `.env` file in the repo root via `python-dotenv`; start from
`.env.example`. The file is gitignored. **Treat it as sensitive — it holds live API keys
and must never be committed or shared.** See the security note at the bottom of this guide.

> **File-based secrets.** Any secret may also be supplied via a `<NAME>_FILE` env var
> (e.g. `OPENAI_API_KEY_FILE`, `ANTHROPIC_API_KEY_FILE`) pointing at a file — a
> Docker/K8s secret mount — which **wins over** the plain env var (`config.read_secret`;
> [Production Hardening §L](../design/Macro_RAG_Production_Hardening.md)).
>
> **Skipping the dotenv file.** `MACRORAG_DOTENV=0` stops both config layers from loading
> the repo-root dotenv file at import, so the process environment is used exactly as
> given. The test suite sets it (with an isolation guard in `test/conftest.py`) so tests
> never see live keys.

### 3.1 Core variables

| Variable | Purpose | Required? |
|---|---|---|
| `OPENAI_API_KEY` | Default LLM provider (query analysis, generation, verify, synthesis) | Yes, unless you run retrieval-only or switch provider |
| `ANTHROPIC_API_KEY` | Alternate LLM provider (set `llm.provider: anthropic` in `configs/rag.yaml`) | Only if using Anthropic |
| `HF_HOME` | HuggingFace cache dir for the bge-m3 + bge-reranker weights (e.g. `/path/to/hf-cache`) | Recommended (controls where ~1.4 GB of weights land) |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default `INFO`) | No |
| `MACRORAG_CONTACT_EMAIL` | Your contact address, sent in the ingest User-Agent (`configs/ingest/common.yaml` reads it as `${MACRORAG_CONTACT_EMAIL:-unset}`). Agencies throttle anonymous crawlers | Before you ingest |

### 3.2 Source-data API keys (only for the tiers you ingest)

These drive ingestion — see [02 – Data Ingestion](02-data-ingestion.md) for the
tier → key mapping. Each tier YAML names the env var it reads (`api_key_env`), and a
missing optional key makes that endpoint skip gracefully rather than fail the run.

| Variable | Service | Used by tier |
|---|---|---|
| `FRED_API_KEY` | FRED daily market series (feeds the market-episode cards) | 3 (us stats) |
| `EIA_API_KEY` | U.S. Energy Information Administration (AEO/STEO) | 6a (power) |
| `BEA_API_KEY` | Bureau of Economic Analysis (NIPA tables) | 3 (us stats) |
| `BLS_API_KEY` | Bureau of Labor Statistics (employment, CPI) | 3 (us stats) |
| `CENSUS_API_KEY` | U.S. Census (BTOS AI-adoption survey) | 6c (ai-macro) |
| `PJM_API_KEY` | PJM Data Miner 2 (grid load feed; not provisioned by default, so the endpoint skips — interconnection-queue signals are not built, [Production Hardening §C](../design/Macro_RAG_Production_Hardening.md)) | 6a (power, optional) |
| `IEA_ACCOUNT` / `IEA_PASSWORD` | International Energy Agency login | 6a (power) |
| `STOOQ_API_KEY`, `FINRA_API_KEY[_SECRET]`, `USITC_ACCOUNT/PASSWORD` | Present in `.env.example` for sibling systems; not consumed by the ingestion handlers | — |

> Tip: run `python -m ingest --tier <tier> --list` to see a tier's endpoints before
> worrying about which keys you need.

---

## 4. The four external dependencies, explained

### 4.1 LLM provider (OpenAI / Anthropic)

Used for: query analysis (paraphrase / decompose / HyDE / routing), answer
generation, faithfulness verification, and shock-vector synthesis. The system uses
**tiered routing** — a cheap model for plan/verify, the answer model for simple
queries, and a frontier model for complex routes — configured as a per-provider
`profiles:` block, so switching `provider` remaps every tier at once.

You need an API key for the active provider (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`).
The full model config (the `profiles:` shape, the current lineup, and why the plan
tier must be capable) is documented once, canonically, in
**[04 §4 — LLM provider & tiered routing](04-query-retrieval-eval.md)**. An optional
quality-signal **escalation** tier and the schema-validated prompt registry are covered
there and in [Macro_RAG_LLM_Harness.md](../design/Macro_RAG_LLM_Harness.md).
LLM **resilience** (retry/backoff, circuit-breaker and explicit timeouts, `llm.resilience.*`)
is documented in [04 §4](04-query-retrieval-eval.md) ([Production Hardening §H](../design/Macro_RAG_Production_Hardening.md)).

> You can run **retrieval-only** with no LLM key at all (`query … --no-llm`, see
> [04](04-query-retrieval-eval.md)). That is the cheapest way to verify ingest +
> index before spending tokens.

### 4.2 Vector store — Qdrant

Stores the narrative chunk embeddings (dense 1024-d + sparse) and serves hybrid
search with server-side Reciprocal Rank Fusion and payload (facet) filters.

Two modes:

| Mode | Config | When |
|---|---|---|
| **Server** (what `configs/rag.yaml` ships) | `qdrant.url: http://localhost:6333` | Every real index build — the embedded mode deadlocks on bulk upsert |
| **Embedded on-disk** (code default when no `url` is set) | `qdrant.path: data/vectorstore/qdrant` | Small single-process experiments only |

Run the Qdrant server with Docker (details in [08](08-operations-and-deployment.md)):

```bash
docker compose -f infra/docker/docker-compose.yml up -d qdrant   # :6333 / :6334
```

Optional `qdrant.quantization: true` enables int8 scalar quantization (~4× memory
saving, ~1% recall cost).

### 4.3 Time-series store — SQLite

Exact numbers (BEA/BLS series, DFAST scenarios, SEP projections, GPR/EPU indices)
are kept out of the vector store and in a SQLite DB at
`data/vectorstore/timeseries.sqlite`. It is built by the same `index` command and
hydrated into answers on demand. No setup — SQLite ships with Python.

### 4.4 Embedding & rerank models (HuggingFace)

| Model | Role | Size |
|---|---|---|
| `BAAI/bge-m3` | One pass → dense (1024-d) + sparse vectors | ~1.1 GB |
| `BAAI/bge-reranker-v2-m3` | Cross-encoder reranker | ~0.3 GB |

They download on first use into `HF_HOME`. Pre-warm by running one `index` or `query`.
The test suite swaps in a hashing embedder + identity reranker, so `pytest` needs no
weights and no network.

### 4.5 Shared inference service — RagInference (default for **indexing and** retrieval)

> **RagInference is a separate project and is not part of this repository.** Without it,
> set `RAGINFER_URL=""` (or leave `fallback_local: true`) and both flows load the models
> in-process — it works on any host with the full `requirements.txt` (torch included).

By **default**, neither indexing nor retrieval loads the two models above in-process — both
call a shared **RagInference** service that holds one bge-m3 + bge-reranker copy (shared with
the sibling XbrlRag system), so a constrained-RAM/CPU desktop doesn't pay for multiple
multi-GB model copies. The service exposes `POST /embed` and `POST /rerank` (plus
`GET /healthz`).

Both flows resolve their embedder through the same `embeddings.select_embedder`, so query
and corpus vectors are guaranteed to come from the same model and stay in one space.

```yaml
inference:
  remote_url: http://localhost:8799   # default; must be running for index AND retrieval
  fallback_local: true                # if unreachable, load the models in-process instead
```

| Setting / env | Effect |
|---|---|
| `inference.remote_url` (config) | Default service base URL |
| `RAGINFER_URL` (env) | **Overrides** the config — a different base URL, or **empty to force in-process models** |
| `inference.fallback_local` / `RAGINFER_FALLBACK_LOCAL` | Load models locally if the service is unreachable (graceful degrade) |
| `RAGINFER_TIMEOUT_S`, `RAGINFER_EMBED_MODEL`, `RAGINFER_RERANK_MODEL` | Per-client timeout / model overrides |

Check it's up: `curl http://localhost:8799/healthz`.

> **Why indexing goes through the service too.** The containerised backend image ships
> **no torch** ([09 §1](09-packaging-and-deployment.md)), so an in-container `index` has no
> model to load. Consequences: (a) the service must be up to build an index, and
> (b) `inference.fallback_local` is forced **off** in the prod config, so a stopped service
> fails the build loudly instead of raising a confusing `ImportError` on absent torch.

---

## 5. Configuration model

There are two layers of configuration:

1. **`configs/rag.yaml`** — overrides for the retrieval/LLM/serving stack. It **ships
   committed with tuned overrides** (Qdrant server URL, shared-inference URL, per-provider
   LLM `profiles:`, the `escalation:` block, `rerank_candidates: 24`, trace/synthesis knobs); code
   defaults in `src/utils/config.py` apply only where the file is silent. Edit it to
   change models, enable auth, point at a different Qdrant, enable Redis/OTel, etc.
2. **`configs/ingest/*.yaml`** — the ingestion tier catalog and controlled
   vocabulary. See [02](02-data-ingestion.md).

To switch to Anthropic, edit `configs/rag.yaml` — with profiles, selecting the provider
auto-remaps every model tier and the key env var:

```yaml
llm:
  provider: anthropic          # remaps plan/answer/complex to the anthropic profile;
                               # api_key_env derives from the provider (ANTHROPIC_API_KEY)
```

### 5.1 Deploy mode — local vs AWS

Everything above is `deploy_mode: local` (the default): all corpus data on the local
filesystem. Setting `deploy_mode: aws` (or `MACRORAG_DEPLOY_MODE=aws`) routes the corpus
**object data** (raw / processed / manifest) to **S3** while the SQLite time-series + traces
stay on the local/EBS filesystem and Qdrant + RagInference run on their own hosts. Local dev
needs none of this; see [08 — Operations & Deployment §2.1](08-operations-and-deployment.md)
and [AWS deployment](../design/Macro_RAG_AWS_Deployment.md).

| Env var | Meaning |
|---|---|
| `MACRORAG_DEPLOY_MODE` | `local` (default) / `aws` |
| `MACRORAG_S3_BUCKET` / `MACRORAG_S3_PREFIX` / `MACRORAG_S3_REGION` | corpus bucket + layout (aws mode) |

S3 credentials come from the ambient AWS chain (EC2 instance role / env), never config;
`boto3` is lazy-imported, so a local install never needs it.

---

## 6. Verify the install

```bash
# 1. unit tests — fakes only, no torch, no network
pytest test/                      # --collect-only prints the current count
mypy src/                         # type gate: must report zero errors

# 2. smoke an ingest of a single endpoint (3 docs max)
python -m ingest --tier tier1_fed --endpoints press_monetary --limit 3

# 3. build the stores  (needs RagInference up — §4.5 — or RAGINFER_URL="")
python -m pipelines.cli index

# 4. retrieval-only query (no LLM key needed)
python -m pipelines.cli query "FOMC statement on inflation" --no-llm
```

If all four succeed you have a working pipeline. Next: [02 – Data Ingestion](02-data-ingestion.md).

---

## 7. Directory map (what lives where)

```
configs/
  rag.yaml            committed, tuned retrieval/LLM/serving overrides
  ingest/*.yaml       tier catalog + common defaults + vocabulary
  eval/               golden sets (golden.yaml default, golden_user_facing.yaml,
                      golden_stress_backend.yaml) + shock_reference.yaml
data/                 (gitignored)
  raw/ingest/         raw bytes, exactly as fetched (audit/replay)
  processed/ingest/   ParsedDocument JSON (chunks + timeseries)  ← index input
  cache/
    ingest_cache.sqlite  conditional-GET validators + processed-doc ledger
  vectorstore/
    qdrant/           embedded Qdrant store (if on-disk mode)
    timeseries.sqlite exact-numbers store
    manifest.json     index metadata (world_view_id, counts)
    manifest_history.jsonl  append-only build log (one line per index build)
  traces/
    queries.jsonl     per-query trace log
    feedback.jsonl    thumbs up/down feedback
  eval/               drift baseline + eval history
src/                  ingest · pipelines · retriever · synthesis · api · …
ui/                   React web UI
infra/                Dockerfile, compose, Helm, OTel config
scripts/              maintenance (rechunk, re-extract, vocab, retag), backup/restore,
                      live question runner, image build/push, S3 corpus sync
prompts/              versioned prompt registry: system/<role>.md +
                      tasks/<task>/{base.md, base.meta.yaml, schema.json}
                      (resolved by src/llm/registry.py)
```

---

## 8. Troubleshooting (setup)

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'utils'/'api'/'pipelines'` | `src/` not on path | `export PYTHONPATH=src` (PowerShell: `$env:PYTHONPATH="src"`); run from repo root |
| First `index`/`query` hangs for minutes | Downloading bge-m3 / reranker weights | Expected once; watch `HF_HOME`. Pre-warm and reuse the cache |
| `OPENAI_API_KEY` errors / 401 from LLM | Key missing or wrong in `.env` | Set `OPENAI_API_KEY`; or run `--no-llm`; or switch provider in `configs/rag.yaml` |
| Out-of-memory / very slow embeddings | CPU-only on a large corpus | Use a CUDA GPU, lower `embedding.batch_size`, or enable `qdrant.quantization` |
| Playwright errors on ingest | Chromium not installed | `playwright install chromium`, or ignore (endpoint skips) |
| `pytest` tries to download torch | You ran tests with real impls | Tests use fakes by default; run `pytest test/` from repo root unmodified |
| Qdrant "already accessed by another instance" (on-disk) | Two processes share `data/vectorstore/qdrant` | Use a single process for on-disk mode, or switch to a Qdrant **server** (`qdrant.url`) |

> ### Security note — keep the keys out of git
> The local `.env` holds **real, live credentials** (OpenAI, FRED, EIA, BEA, BLS, Census,
> plus IEA/USITC logins). It is gitignored and must stay untracked; if a key is ever
> exposed, rotate it. For non-local environments use a secret manager — the supported
> non-dotenv path is file-based secret mounts via `<NAME>_FILE` env vars (§3,
> `config.read_secret`), which override the plain vars. Never paste keys into logs,
> screenshots, or external services.
