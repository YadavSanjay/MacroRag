<h1 align="center">MacroRAG</h1>

<p align="center">
  <b>Macro research in, defensible stress scenarios out.</b><br>
  A retrieval-augmented research desk over central-bank, statistical-agency, IMF/BIS, sell-side,
  energy and chip-supply-chain publications. It answers macro questions with citations and turns
  scenarios into <b>citation-gated shock vectors</b>: signed, unit-checked factor moves that a stress
  engine can price, each one traceable to a dated source passage.
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Qdrant hybrid search" src="https://img.shields.io/badge/search-Qdrant%20hybrid-dc244c">
  <img alt="bge-m3 + reranker" src="https://img.shields.io/badge/models-bge--m3%20%2B%20reranker-1f6feb">
  <img alt="REST + MCP" src="https://img.shields.io/badge/API-REST%20%2B%20MCP-009688?logo=fastapi&logoColor=white">
  <img alt="React + TypeScript" src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-61DAFB?logo=react&logoColor=black">
  <img alt="mypy zero errors" src="https://img.shields.io/badge/mypy-zero%20errors-2a6db2">
  <img alt="Free public data" src="https://img.shields.io/badge/data-free%20%26%20public-2ea043">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-lightgrey"></a>
</p>

> **Nothing here is investment advice. Answers and shock vectors are model output over public
> research and can be wrong, stale or incomplete. Check the citations before you rely on a number.**

---

## Why MacroRAG

Every stress-testing programme has a hand-off nobody owns. The Fed publishes a severely adverse
path, the IMF and BIS publish risk narratives, a dozen sell-side desks publish house views, and
somewhere an analyst reads all of it and types *"10Y +120bp, HY +450bp, VIX 45"* into a scenario
file. That step is slow, rests on one person's judgement, and leaves no record of why each number
was chosen. MacroRAG replaces it:

- **Numbers with a paper trail.** Every shock-vector field cites dated source passages returned in
  the same response. A field that cannot be walked back to a dated passage is dropped, not flagged.
- **The model doesn't decide what ships.** A deterministic gate ladder (fixed vocabulary, citation
  gate, plausibility band, series anchor, cross-field coherence, horizon and direction checks) sits
  between the LLM and the output. It is ordinary Python a model-risk reviewer can read.
- **Prose and exact numbers kept apart.** Narrative goes into a hybrid vector index; official paths
  and series go into a time-series store and are hydrated into answers verbatim, so "what did DFAST
  assume for unemployment?" is answered from the table, not from the model's memory.
- **Built for the AI trade.** The corpus is weighted to the channels an AI-themed book actually
  carries: discount rate and term premium, rates vol, credit spreads, AI capex, the power constraint,
  and China/Taiwan export-control risk.
- **Traceable six months later.** Every result carries a content-addressed `world_view_id` for the
  corpus it was built on, every LLM call is stamped with its prompt version and hash, and every query
  leaves a full trace. The build history proves what a past corpus was; it is not a snapshot store, so
  a superseded corpus cannot be queried again.
- **Honest about accuracy.** Fidelity to official projections, a backtest against what markets
  actually did, and a judge-free self-check are measured separately and never averaged together.

## Questions it answers

| You want to know… | MacroRAG gives you… |
|---|---|
| What does the 2026 DFAST severely adverse scenario assume for unemployment and the 10Y? | A cited answer with the exact quarterly path from the time-series store |
| What moves should I apply to rates, credit, vol and FX for a Taiwan export-control shock over 10 trading days? | A gated shock vector: each field signed, unit-checked, cited and labelled *direct* or *analog* |
| Which macro risks matter for a book long semis, hyperscalers and AI-capex credit? | Ranked, cited risk factors to shock, before you choose magnitudes |
| Where did that +90bp IG widening come from? | One call to resolve the citation: publisher, section, URL and release date |
| Was this vector built on the corpus we have today? | `world_view_id` plus the manifest history: *was this ever our corpus, and is it still live?* |
| Why did a field come back empty? | Per-field diagnostics naming the stage that dropped it and why |

## The shock vector

The headline output: a set of named factor moves over a stated horizon, drawn from a fixed
**19-field vocabulary** that covers a rates/credit/vol book.

```json
{
  "shock_vector": {
    "USDTWD": { "value": 8.0,  "unit": "pct", "chunk_ids": ["bis::045"], "grounding_mode": "analog" },
    "IG_OAS": { "value": 90.0, "unit": "bp",  "chunk_ids": ["fsr::012"], "grounding_mode": "direct" },
    "VIX":    { "value": 18.0, "unit": "pts", "chunk_ids": ["fsr::012"], "grounding_mode": "direct" }
  },
  "world_view_id": "macro-2026-06-23-9f2c1ab77e04"
}
```

The fields span the Treasury curve (with the 10Y split into real yield and term premium), IG and HY
spreads, a rates-vol term structure by swaption expiry, US equity level and VIX, and USD/TWD,
USD/JPY and cross-currency basis. Units and sign conventions are fixed per field and published machine-readably, so a consumer never
guesses what "+8" means. Caller controls are structural, not prose: restrict `fields` to what your
book carries, pin `horizon_days`, add `scenario` narrative, and state expected `directions` so a
field that moves the wrong way is rejected.

Short horizons need short-horizon evidence, so the corpus also holds **market-episode cards**: the
realized 5- and 10-trading-day moves around named stress events (Lehman, COVID, SVB, the 2024 yen-carry
unwind, DeepSeek, the 2025 tariff shock and others), citable like any other document. The field
table and the full contract are in [guide 05 §5](docs/guides/05-api-and-integration.md).

---

## Architecture at a glance

Sources flow through ingestion into two stores (prose and exact numbers), stamped with a
content-addressed corpus identity. One retrieval pipeline feeds both outputs:

<p align="center">
  <a href="docs/images/architecture.svg">
    <img src="docs/images/architecture.svg" width="100%"
         alt="MacroRAG architecture: free public macro sources feed nine tiers of ingest handlers into a local-or-S3 object store. The index builds a Qdrant hybrid vector index for prose and a SQLite time-series store for exact numbers, stamped with a content-addressed world_view_id. Retrieval analyses the question, runs hybrid search with RRF fusion, reranks against a relevance floor, ranks and diversifies, and hydrates exact series. It feeds a faithfulness-checked cited answer and a shock vector that passes a deterministic gate ladder before reaching the faultline stress engine. Everything is served over REST, MCP, a CLI and a React UI, with evaluation and a provenance spine alongside.">
  </a>
</p>

- **One pipeline, four surfaces.** A FastAPI REST API (with streaming), an MCP server so an LLM
  scenario agent can call MacroRAG as a tool, a CLI, and a React UI: *Ask*, *Factors*, *Scenario
  Studio* and *Corpus* for analysts; *Quality*, *Traces*, *Health* and *Stores* for whoever runs it.
- **Every external dependency sits behind an interface.** Embedder, vector store, reranker, LLM and
  object storage each have a fake, so the full test suite runs with no torch, no GPU and no network.
- **Laptop or AWS.** The same images run locally or with the corpus on S3 (`deploy_mode: local | aws`).
  Embedding and reranking come from a shared inference service, so the API image stays small.

| Concern | Choice |
|---|---|
| Embeddings | `BAAI/bge-m3`: dense (1024-d) and sparse in one pass |
| Search | Qdrant hybrid with server-side RRF fusion and facet filters |
| Rerank | `BAAI/bge-reranker-v2-m3` cross-encoder with a relevance floor that refuses rather than guesses |
| Generation | OpenAI or Anthropic, tiered by task: a capable model to plan and verify, a cheap one for simple answers, a frontier one for synthesis |
| Numbers | SQLite time-series store, hydrated into answers by query cue |
| Controls | LLM retry and circuit breaker, hard daily cost caps, prompt-injection fencing of retrieved text, Prometheus metrics, per-query traces |

## The corpus

**43 configured endpoints across nine source tiers**, all free: public-domain government output, free
research, or free with an account, with a licence flag on every chunk. Not every endpoint yields data:
CBO, FERC 714, PJM and Census BTOS are configured but currently return nothing, and sites that refuse
an identified crawler are skipped ([known gaps](docs/design/Macro_RAG_Source_Catalog.md#6-known-gaps-and-open-items)).

| Tier | Sources |
|---|---|
| Federal Reserve | FOMC statements and minutes, SEP, Monetary Policy Report, speeches, FSR, SLOOS, DFAST |
| Treasury | Quarterly refunding / TBAC, TIC, OISP |
| US statistics | BEA, BLS, CBO, FRED daily series, NY Fed ACM term premium, market-episode cards |
| International | IMF reports and SDMX data, BIS Quarterly and Annual reviews, Bank of Japan Outlook |
| House views | BlackRock, J.P. Morgan, Goldman Sachs, Morgan Stanley, Apollo |
| Power | EIA AEO and STEO, IEA, FERC 714, PJM |
| Chips and export controls | Federal Register, BIS Entity List, SIA, TSMC |
| AI macro | GPR and EPU indices, Census BTOS, Stanford HAI |
| Scenario studies | EBA/ESRB adverse scenario, IMF GFSR geopolitical, Taiwan-crisis studies (St. Louis Fed, Rhodium, HCSS, CSIS) |

Why each source is there and which risk channel it covers: [Source Catalog](docs/design/Macro_RAG_Source_Catalog.md).

## How it is measured

| Evidence | What it tells you |
|---|---|
| Retrieval metrics (hit@k, recall@k, MRR) on two golden sets, with a CI drift gate | Whether the right passages come back |
| LLM-judged faithfulness and relevancy, plus a prompt × model regression matrix | Whether answer quality holds across prompt versions and models |
| Transcription fidelity against DFAST/EBA baseline → adverse paths | Whether official scenarios are read and transcribed correctly (not forecast skill) |
| Realized-episode backtest over 1976–2025 history, in `grounding_fidelity` and `ex_ante_skill` modes | Whether shocks match what markets actually did, with and without hindsight |
| Self-check against the time-series store, with negative controls | Known-answer questions at no labelling cost; flags fabrication on false premises |

---

## Quick start

Full setup, secrets and the external services are in
[guide 01](docs/guides/01-setup-and-prerequisites.md). The essentials:

```bash
# Python 3.12 env (here: conda env py312). Set OPENAI_API_KEY (and any source keys) in .env.
pip install -r requirements.txt
cp .env.example .env                                        # add OPENAI_API_KEY and MACRORAG_CONTACT_EMAIL
docker compose -f infra/docker/docker-compose.yml up -d qdrant   # vector store on :6333
# Embedding and rerank go through the shared RagInference service on :8799 by default (a
# separate project, not included here); set RAGINFER_URL="" to load the models in-process
# instead (guide 01 §4.5).

export PYTHONPATH=src                                       # PowerShell: $env:PYTHONPATH="src"
python -m ingest --tier tier1_fed                           # fetch the Federal Reserve tier
python -m pipelines.cli index                               # build the vector + time-series stores
python -m pipelines.cli query "severely adverse unemployment path in DFAST 2026"
python -m pipelines.cli synthesize "Taiwan export-control escalation" --horizon-days 10
python -m pipelines.cli serve                               # REST API on http://127.0.0.1:8000

# UI (Node 24.14.0): http://localhost:5173, proxies /api to :8000
npm install --prefix ui && npm run dev --prefix ui
```

## Explore further

| If you want to… | Start here |
|---|---|
| Use the UI as an analyst | [Business user guide](docs/guides/07-business-user-guide.md) |
| Ingest, index and tune retrieval | [Operator guides](docs/guides/README.md) · [ingestion](docs/guides/02-data-ingestion.md) · [indexing](docs/guides/03-indexing.md) · [query, LLM tiers and eval](docs/guides/04-query-retrieval-eval.md) |
| Integrate a stress engine or LLM agent | [REST, MCP and the shock-vector contract](docs/guides/05-api-and-integration.md) |
| Understand the design decisions | [Pipeline](docs/design/Macro_RAG_pipeline_design.md) · [LLM harness](docs/design/Macro_RAG_LLM_Harness.md) · [production hardening](docs/design/Macro_RAG_Production_Hardening.md) · [ingestion](docs/design/Macro_RAG_Ingestion_Design.md) |
| Read the case for it, with every claim tied to code | [Positioning and capability brief](docs/Macro_RAG_Positioning.md) |
| Deploy with Docker, Helm or AWS | [Operations](docs/guides/08-operations-and-deployment.md) · [packaging and deployment](docs/guides/09-packaging-and-deployment.md) · [AWS design](docs/design/Macro_RAG_AWS_Deployment.md) |
| Work in the code with Claude Code | [CLAUDE.md](CLAUDE.md) and the per-module `CLAUDE.md` files |

<details>
<summary><b>Repository layout</b></summary>

```
src/ingest/        tiered source handlers → ParsedDocument JSON (prose chunks + numeric series)
src/embeddings/    bge-m3, remote (RagInference) and hashing (test) embedders
src/vectorstores/  Qdrant hybrid store, in-memory fake, SQLite time-series store
src/retriever/     query analysis, hybrid search, RRF fusion, rerank, time-decay, MMR, multi-hop
src/agent/         typed plan-execute agent with a safe calculator
src/synthesis/     shock-vector synthesizer, citation gate, validation, vocabulary, diagnostics
src/factors/       cited risk-factor discovery
src/llm/           provider clients, versioned prompt registry, verify, escalation, resilience
src/pipelines/     CLI, index and query pipelines, time-series hydration
src/api/           FastAPI app, admin and job endpoints      src/mcp_server/  MCP tools
src/eval/          retrieval metrics, judge, drift, matrix, shock eval, episodes, self-check
src/utils/         config, object store (local | S3), manifest, trace, metrics, cost guard
prompts/           versioned prompts: system roles + per-task templates and JSON schemas
ui/                React + TypeScript web UI
configs/           rag.yaml runtime tuning, ingest tiers and vocabulary, eval golden sets
docs/              guides/ (how to run) · design/ (why) · specs/ (test-first change specs)
infra/             backend + UI Dockerfiles, compose, Helm, OTel, AWS overlay
test/              unit and integration tests, fakes only; spec/ holds locked spec tests
```

</details>

<details>
<summary><b>Engineering and quality gate</b></summary>

- **Gate:** `ruff check` → `mypy src/` (zero errors, config in [mypy.ini](mypy.ini)) → `pytest test/`.
  CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) also type-checks and builds the UI,
  builds both container images and runs advisory standards and `pip-audit` jobs.
- **Tests use fakes, not services:** a hashing embedder, an in-memory vector store, an identity
  reranker and a scripted LLM, so the suite needs no torch, no model download and no network.
- **Test-first changes:** each change starts from a written spec and locked spec tests
  ([.claude/tdd/](.claude/tdd/)), so behaviour is pinned before the code is written.
- **Prompts are versioned files**, not string literals; every call records the prompt variant and
  content hash, and a hash of the whole registry goes into the index manifest.
- **Requirements:** [requirements.txt](requirements.txt) (full stack) ·
  [requirements-serve.txt](requirements-serve.txt) (lightweight API image, no torch) ·
  [requirements-dev.txt](requirements-dev.txt) (ruff / mypy / pytest).

</details>

---

## License and support

MIT — see [`LICENSE`](LICENSE).

**Source data.** No source documents are committed: the ingest handlers download public material
into the gitignored `data/` tree on your machine, and you are responsible for following each
source's terms of use. Some sources need a free API key (FRED, BEA, BLS, EIA, Census and others);
see [`.env.example`](.env.example) and [guide 01 §3](docs/guides/01-setup-and-prerequisites.md).
That includes redistributing extracted text and running a tier on a schedule; set
`MACRORAG_CONTACT_EMAIL` so sites can reach you. Sell-side research is used only where it is
published openly, with its licence recorded on every chunk; the flag is a record, not a control.

**Security.** Auth is off by default, including the `/admin` surface. The compose stack binds to
`127.0.0.1` for that reason; turn auth on ([guide 05 §2](docs/guides/05-api-and-integration.md))
before exposing it to a network, because an open API spends your LLM budget.

**Companion systems are not included.** The faultline stress engine (the consumer of shock
vectors) and the RagInference embedding service are separate projects; MacroRAG runs without
either (see Quick start).

This is a personal project, shared as-is: no support, no stability guarantees, and no promise to
review issues or pull requests. Not investment advice (see the note at the top). Security issues:
see [SECURITY.md](SECURITY.md).
