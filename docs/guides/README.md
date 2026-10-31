# MacroRAG Guides

Operator and user documentation for **MacroRAG** — the macro-economic
retrieval-augmented generation system that turns central-bank, statistical-agency,
sell-side and supply-chain research into **cited answers** and **structured, cited
shock vectors** for the downstream stress-testing engine (faultline).

These guides are task-oriented: each one walks an operator or user end-to-end
through one part of the system, with exact commands, the configuration that drives
it, and a troubleshooting section for the issues that actually come up with RAG
systems in production.

---

## Who should read what

| You want to… | Read |
|---|---|
| Install the system and understand what it depends on | [01 – Setup & Prerequisites](01-setup-and-prerequisites.md) |
| Download source data (full from scratch, or incremental) | [02 – Data Ingestion](02-data-ingestion.md) |
| Build / update the vector DB and time-series store | [03 – Indexing](03-indexing.md) |
| Ask questions from the CLI, tune retrieval, run eval | [04 – Query, Retrieval & Eval](04-query-retrieval-eval.md) |
| Call MacroRAG from another service (e.g. faultline) | [05 – REST API & Integration](05-api-and-integration.md) |
| Run or develop the web UI | [06 – Web UI](06-ui.md) |
| Use the product as a (non-technical) analyst | [07 – Business User Guide](07-business-user-guide.md) |
| Deploy, monitor and run the system in production | [08 – Operations & Deployment](08-operations-and-deployment.md) |
| Build the container images, push to AWS ECR, deploy on EC2 | [09 – Packaging & Deployment](09-packaging-and-deployment.md) |

---

## The pipeline in one picture

```
  ┌────────────┐  fetch + parse   ┌──────────────┐  embed + upsert  ┌──────────────────┐
  │  Sources   │ ───────────────► │ data/        │ ───────────────► │ Qdrant (vectors) │
  │ (9 configs)│   §02 Ingest     │ processed/   │   §03 Index      │ + SQLite (series)│
  └────────────┘                  └──────────────┘                  └────────┬─────────┘
                                                                              │
   ┌──────────────────────────────────────────────────────────────────┐     │ retrieve
   │ Retrieval → rerank → time-decay → MMR → expand → relevance gate    │ ◄───┘
   │ Generate (cited answer)  |  Synthesize (cited shock vector)        │
   └───────────────────────┬───────────────────────────┬──────────────┘
                           │ §04 CLI / §05 REST API     │ §05 mode=synthesize → faultline
                  ┌────────▼────────┐          ┌─────────▼──────────┐
                  │ FastAPI server  │ ◄──────► │ React Web UI (§06) │
                  └─────────────────┘  /api    └────────────────────┘
```

The **end-to-end happy path** is: **ingest → index → query/serve → UI**. Each arrow
is one guide.

---

## 60-second quick start

> Assumes Python 3.12 and the Node toolchain are installed and an `OPENAI_API_KEY`
> is set. Full details in [01 – Setup & Prerequisites](01-setup-and-prerequisites.md).
>
> **Also start RagInference first.** By default both `index` and every query embed through
> the shared inference service on `http://localhost:8799`; set `RAGINFER_URL=""` to load
> bge-m3 in-process instead ([01 §4.5](01-setup-and-prerequisites.md)).

```bash
# 0. backend deps (run everything from the repo root)
pip install -r requirements.txt

# 1. download some sources (Tier 1 = Federal Reserve)
python -m ingest --tier tier1_fed

# 2. build the vector + time-series stores (embeds via RagInference)
python -m pipelines.cli index

# 3a. ask a question from the CLI
python -m pipelines.cli query "severely adverse unemployment path in DFAST 2026"

# 3b. or serve the REST API (http://127.0.0.1:8000)
python -m pipelines.cli serve

# 4. (separate terminal) run the web UI -> http://localhost:5173
npm install      --prefix ui
npm run dev      --prefix ui
```

---

## What the system depends on (at a glance)

| Dependency | Role | Required? | Where it's covered |
|---|---|---|---|
| **Python 3.12** | Backend runtime (ingest, index, retrieval, API) | Yes | [01](01-setup-and-prerequisites.md) |
| **LLM API key** (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) | Query analysis, generation, faithfulness check, synthesis | Yes (for generation; retrieval-only works without) | [01](01-setup-and-prerequisites.md), [04](04-query-retrieval-eval.md) |
| **Qdrant** | Vector store (hybrid dense+sparse RRF) | Yes — a Qdrant server (`configs/rag.yaml` → `localhost:6333`); the embedded on-disk mode is for small local runs only | [01](01-setup-and-prerequisites.md), [03](03-indexing.md) |
| **SQLite** (stdlib) | Time-series store for exact numbers | Yes — bundled with Python | [03](03-indexing.md) |
| **RagInference** service | Shared bge-m3 embedder + bge-reranker, used by **both** indexing and retrieval | Yes by default (`RAGINFER_URL=""` forces in-process models instead) | [01 §4.5](01-setup-and-prerequisites.md) |
| **HuggingFace model cache** (`HF_HOME`) | Where the bge weights live — on the **RagInference host** by default; only on this host if you force in-process models | Only when running models in-process (fakes used in tests) | [01](01-setup-and-prerequisites.md) |
| **Node.js toolchain** | Build/run the web UI | Only for the UI | [06](06-ui.md) |
| **Source-data API keys** (FRED/EIA/BEA/BLS/CENSUS/IEA…) | Per-tier ingestion | Only for the tiers you ingest | [02](02-data-ingestion.md); template in `.env.example` |
| **Docker** | Build/run the containerised stack (backend + UI images) | Only for containerised/AWS deploys | [09](09-packaging-and-deployment.md) |
| **S3 bucket** | Corpus object storage in `deploy_mode: aws` (raw/processed/manifest) | Only in AWS mode | [09](09-packaging-and-deployment.md); [AWS deployment §4](../design/Macro_RAG_AWS_Deployment.md) |
| **Redis** | Optional shared state — query cache, rate-limit buckets, job lock/store + idempotency, semantic cache, cost counters (one `redis.url` / `MACRORAG_REDIS_URL`, all fall back in-process) | No (in-process fallback) | [08](08-operations-and-deployment.md); [Production Hardening §I](../design/Macro_RAG_Production_Hardening.md) |
| **OpenTelemetry collector** | Optional distributed tracing (the collector exports to Tempo and a debug log) | No | [08](08-operations-and-deployment.md); [Production Hardening §J](../design/Macro_RAG_Production_Hardening.md) |
| **GPU (CUDA)** | Faster embeddings/rerank | No (CPU works, slower) | [01](01-setup-and-prerequisites.md) |

---

## Design docs

These guides are **operational** — they describe how the system behaves today and are the
canonical home for their topic ([the ownership map is in `CLAUDE.md`](../../CLAUDE.md#documentation-map--one-owner-per-topic)).
For rationale, deeper specs, and why something is the way it is:

- [Project README](../../README.md) — architecture at a glance
- [Ingestion design](../design/Macro_RAG_Ingestion_Design.md) · [Source catalog](../design/Macro_RAG_Source_Catalog.md)
- [Retrieval / generation pipeline design](../design/Macro_RAG_pipeline_design.md)
- [LLM harness, prompt registry & escalation](../design/Macro_RAG_LLM_Harness.md)
- [Web UI design](../design/Macro_RAG_UI_Design.md) · [UI containerization](../design/UI_Containerization.md)
- [AWS deployment](../design/Macro_RAG_AWS_Deployment.md) — §4 is the authoritative list of AWS resources infra must provision
- [Production hardening](../design/Macro_RAG_Production_Hardening.md) — resilience, shared state, observability, governance and security; guides cite its sections as `§A`…`§L`
- [Infra notes](../../infra/README.md) — what is in `infra/`, and where each artifact is documented

---

## Conventions used in these guides

- All commands assume your working directory is the **repo root** unless stated
  otherwise.
- `python -m …` invocations require `src/` on the import path. The CLIs are designed
  to be run from the repo root; if you hit `ModuleNotFoundError`, set
  `PYTHONPATH=src` (see [01](01-setup-and-prerequisites.md)).
- Node/npm commands use `--prefix ui` rather than `cd ui`, so your shell stays at the
  repo root. Node 24.x / npm 11 must be on `PATH`.
- Commands are shown for a POSIX-style shell; on PowerShell, environment-variable
  syntax differs (`$env:NAME = "x"` instead of `NAME=x`). Both shells are available.
