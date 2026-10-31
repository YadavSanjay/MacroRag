# MacroRAG — Retrieval, Generation and Synthesis Pipeline

Everything after ingestion: the data contract, embeddings, the vector and time-series stores,
retrieval, the quantitative agent, cited generation, faithfulness checking, shock-vector
synthesis, caching and evaluation. Ingestion is the [Ingestion Design](Macro_RAG_Ingestion_Design.md);
the LLM layer under every generation step is the [LLM Harness](Macro_RAG_LLM_Harness.md); running
it as a service is [Production Hardening](Macro_RAG_Production_Hardening.md).

**Operational detail lives in the guides:** retrieval tuning and LLM tiers in
[guide 04](../guides/04-query-retrieval-eval.md); every endpoint and the synthesis contract in
[guide 05](../guides/05-api-and-integration.md). The package map is in
[CLAUDE.md](../../CLAUDE.md#layout). Where this doc and a guide disagree, the guide (and then the
code) wins.

---

## 1. Overview

Ingestion produces, per source document, a `ParsedDocument` with two payloads:

- **`chunks`** — retrieval-sized narrative text with rich metadata (publisher, publication,
  `release_date`, tier, bucket, doc type, section, `factor_tags`, `entity_tags`).
- **`timeseries`** — exact numeric observations (DFAST paths and history, SEP projections,
  BEA/BLS, FRED and ACM daily, IMF SDMX, EIA, GPR/EPU).

Prose becomes a **hybrid vector index**; numbers become a **queryable time-series store**.
Questions are answered by filtered hybrid retrieval → cross-encoder rerank → cited generation →
faithfulness check, with exact series hydrated in when the question is quantitative. The headline
output is the **citation-gated shock vector** (§9).

The two MacroRAG-specific ideas: the ingestion controlled vocabulary **is** the query filter
language (facet-rich filtering), and numbers are served from a structured store rather than
recovered from prose or table images.

## 2. Interfaces and fakes

Every external dependency sits behind an interface with a test fake:

| Interface | Production | Test fake |
|---|---|---|
| Embedder (`embeddings/base.py`) | `RemoteEmbedder` → RagInference (bge-m3); `BGEM3Embedder` in-process fallback | `HashingEmbedder` |
| Vector store (`vectorstores/base.py`) | `QdrantStore` | `InMemoryVectorStore` |
| Reranker (`retriever/rerank.py`) | `RemoteReranker` → RagInference (bge-reranker-v2-m3); `BGEReranker` fallback | identity reranker |
| LLM (`llm/base.py`) | OpenAI / Anthropic clients | `FakeLLM` |
| Object store (`utils/objectstore.py`) | Local FS / S3 | `InMemoryObjectStore` |

The whole pipeline therefore runs under test with **no torch and no network**.
`embeddings.select_embedder` is used by both indexing and retrieval, so corpus and query vectors
always come from the same model.

## 3. Data contract (ingest → RAG)

`utils/corpus.py` reads the persisted ingest JSON (through the object store, not the ingest
models, so the layers stay decoupled) and yields:

- a **`ChunkPoint`** per chunk: deterministic `point_id` (UUID5 of `chunk_id`), raw `text`,
  `embed_text` (grounding prefix + text), and a `payload` carrying the facets, provenance,
  `content_hash`, and `prev_chunk_id` / `next_chunk_id` for neighbour expansion;
- **time-series rows**: each observation flattened with its document provenance.

The grounding prefix (`publisher | publication | release_date | section`) gives the encoder
entity- and date-aware context.

## 4. Indexing

`pipelines/index_pipeline.py` has two paths:

- **Full rebuild** (`index`, or `index --append` to keep the collection) re-embeds the whole corpus.
  Use it after an embedder or chunking change.
- **Incremental** (`index --incremental [--dry-run]`) diffs each chunk's `content_hash` against
  the store, embeds only new and changed chunks, and deletes departed ones.

Each build writes the **manifest** (`utils/manifest.py`: pipeline version, embedding model/dim/
grounding flag, reranker, LLM tiers, prompt hash, collection, counts, `world_view_id`) and appends
to `manifest_history.jsonl`. `is_stale` flags an embedding-signature change, which requires a full
rebuild. `world_view_id` is content-addressed — `macro-<YYYY-MM-DD>-<12-hex digest>` over the
sorted `(chunk_id, content_hash)` pairs — so an unchanged corpus keeps its id. It is a provenance
stamp, not a snapshot you can query against ([Production Hardening §F](Macro_RAG_Production_Hardening.md#f--reproducibility-world_view_id)).

## 5. Stores

### 5.1 Qdrant (`vectorstores/qdrant_store.py`)

- **One collection, two named vectors:** `dense` (1024-d cosine) and `sparse` (bge-m3 lexical
  weights) — one forward pass yields both, so no separate BM25 layer is needed.
- **Hybrid search** through the Query API: a dense and a sparse prefetch, each pre-filtered,
  fused server-side with RRF.
- **Facet filter** from `MetaFilter`: `MatchAny` on tier, bucket, doc type, publisher, and the
  list fields `entity_tags` / `factor_tags`; a datetime range on `release_dt` (an RFC 3339 mirror
  of `release_date`); the dormant `tenant_id OR shared` clause
  ([Production Hardening §A](Macro_RAG_Production_Hardening.md#a--data-scope-principal-vs-tenant)).
  Payload indexes back each facet.
- **Bulk load:** create with HNSW `m=0`, then `finalize_index()` sets `m=16` once — a large
  speedup. Optional int8 scalar quantization (`qdrant.quantization`).
- **Server mode is required** for real loads (the local on-disk mode deadlocks on bulk upsert);
  `configs/rag.yaml` points at `http://localhost:6333`. `:memory:` serves the adapter tests.

### 5.2 Time-series store (`vectorstores/timeseries_store.py`)

SQLite over the numeric rows: coalesced `source`, `variable`, `scenario`, `period`, `value`
columns for filtering plus the full original row as JSON. It holds the exact numbers prose
retrieval cannot — the DFAST severely adverse unemployment path, the SEP fed-funds dots, the
FRED daily levels behind the episode cards.

## 6. Retrieval (`retriever/retriever.py`)

1. **Analyze.** Always: rule-based facet extraction (`retriever/analysis.py`) — controlled
   vocabulary, synonyms and year windows in the query text → a `MetaFilter`. Optionally one LLM
   planning call (`llm/understanding.py`, `plan` task) adds the route (`DIRECT`, `PARAPHRASE`,
   `DECOMPOSE`, `DECOMPOSE_AND_PARAPHRASE`, `HYDE`), a paraphrase, up to five sub-queries and up
   to two HyDE passages. API tag filters are leniently normalized
   (`utils.config.normalize_api_tags`, e.g. `hyperscalers → hyperscaler`, `taiwan → china`);
   unknown tokens are kept.
2. **Per-branch hybrid search.** All search variants are embedded in one batched call, then the
   filtered hybrid searches run concurrently on a thread pool.
3. **Cross-branch RRF** (`retriever/fusion.py`) with chunk dedup.
4. **Rerank** the pooled candidates (`retrieval.rerank_candidates`, tuned to 24 because CPU
   rerank is the latency bottleneck) against the **original** question.
5. **Time decay** (`retriever/ranking.py`, off by default locally): a bucket-aware recency boost
   so the latest edition of a periodic report outranks a stale one.
6. **MMR** (`retriever/mmr.py`): available, off by default (`mmr_lambda = 1.0`).
7. **Neighbour expansion:** pull the `prev`/`next` chunks of the winners.
8. **Recall guard:** if a strict filter starves retrieval, retry once unfiltered.

**Multi-hop** (`retriever/multihop.py`, `retrieval.multi_hop_max_hops`, off by default) walks a
causal chain — each next sub-question is proposed from the evidence so far — bounded by the hop
count; each hop costs an LLM call and a rerank.

## 7. Quantitative path — typed plan-and-execute (`agent/`)

When a heuristic gate flags a query as quantitative, the pipeline routes it to the agent
(`PLAN_EXECUTE`, generated on the complex tier). An LLM planner emits a **typed plan**:
`DataStep`s (time-series lookups reduced to scalars by `value/max/min/last/mean`), `ComputeStep`s
(a **safe AST calculator** — whitelisted operators and functions, no `eval`), and `RetrieveStep`s
(narrative context). The executor runs them and hands the computed values to generation as an
authoritative, citable `COMPUTED` DATA block, so arithmetic is deterministic rather than the
model's mental math. Non-quantitative queries skip the agent.

## 8. Answering (`pipelines/query_pipeline.py`)

```
cache lookup (L1 exact, L2 semantic)
 → analyze / route  → agent (quantitative) or retrieve
 → relevance-floor gate  (refuse without generating if the top rerank score < min_rerank_score
                          and there are no structured hits; 0 = off)
 → hydrate time series   (pipelines/hydrate.py: cues like "severely adverse", "dot plot",
                          "unemployment" → store filters)
 → generate              (answer task; complex tier for complex_routes)
 → verify faithfulness   (llm/verify.py: each claim checked against its cited chunks AND the
                          hydrated series; unsupported claims dropped, fail-open)
 → escalate if deteriorated (optional; LLM Harness §6)
 → trace + cache write
```

**Output:** `{summary, details[{text, chunk_ids}], unanswered}` plus `unsupported[]` (claims the
verifier dropped), `refused`, the retrieved `chunks[]`, hydrated `timeseries[]` (with plottable
`series` points), `route` and `cached`. Citations are chunk-level; numeric claims cite the
hydrated series. Retrieved text reaches the prompt only through `format_context` /
`fence_untrusted` ([Production Hardening §D](Macro_RAG_Production_Hardening.md#d--prompt-injection-defence)).

**Trace.** Every query writes a JSONL record (`utils/trace.py` → `data/traces/queries.jsonl`):
route, filters, chunk ids and scores, hydrated series, refusal and verify outcome, escalations,
per-stage `timings_ms`, token usage and cost, and (when `trace.capture_llm`) each LLM call's
prompt and response.

**Cache.** L1 is exact (normalized query + filter scope), on by default; L2 is semantic (query
embedding cosine ≥ 0.97), off by default. Both are memory- or Redis-backed, and keys change with
the corpus, the answer prompt hash and the model tier, so a re-index or prompt change
self-invalidates. A synthesize request never reuses a plain answer.

## 9. Shock-vector synthesis (`synthesis/`)

faultline sources correlated stress-scenario drivers from MacroRAG as structured numbers with
citations. Synthesis is `POST /query` with `"mode": "synthesize"` (plus optional `horizon_days`,
`scenario`, a field subset and per-field `directions`); the response keeps every answer field and
adds `shock_vector`, `world_view_id` and `diagnostics`. Field names, units and signs are defined
in `synthesis/vocab.py` (19 fields) and tabulated in
[guide 05 §5](../guides/05-api-and-integration.md).

**Flow** (`query_pipeline.synthesize`):

1. **Retrieve** as for an answer, with two synthesis-specific adjustments: up to
   `synthesis.scenario_study_slots` (2) winner slots are reserved for quantified scenario studies
   (`cross_asset_scenario` / `stress_scenario` tags — Tier 7 studies and market-episode cards)
   found by one extra tag-restricted search, so magnitude anchors are not outranked by closer
   prose; and a smaller rerank pool (`synthesis.rerank_candidates`, 12) keeps latency inside the
   consumer's timeout.
2. **Synthesize** (`synthesizer.py`, `synthesize` task, complex tier): one JSON-constrained
   generation over the **same** chunks and hydrated series — never NLP-parsing of the prose. Each
   field carries `value`, `unit`, `chunk_ids`, `grounding_mode` (direct or analog/proxy, enabled by
   `synthesis.enable_analog`), `source_horizon_days` and a rationale. Unknown fields and units are
   rejected against the vocabulary.
3. **Citation gate** (`gate.py`): a field must cite ≥ `min_citation_chunks` chunk ids, each present
   in the response `chunks[]` **with a `release_date`**; otherwise it is dropped. Freshness
   against a valuation date is the consumer's call.
4. **Grounding reconciliation:** a field labelled analog is upgraded to direct when its own
   scenario series was in the hydrated data and the sign agrees (only upgrades).
5. **Validation** (`validate.py`):
   - *plausibility band* — a per-field cap scaled ~√(horizon / 90 days), capped at 3×, with
     short-horizon floors → `implausible_magnitude`;
   - *unit scale* → `unit_scale_mismatch`;
   - *series anchor* — a move that dwarfs a hydrated series' observed span;
   - *cross-field coherence* — e.g. IG and HY OAS moving in opposite directions;
   - *evidence horizon* — the evidence horizon (a chunk's own `horizon_days`, else a doc-type
     minimum such as DFAST 90 / EBA 365 days, else the model's `source_horizon_days`) more than
     `horizon_tolerance` (3×) away from the requested horizon → `horizon_mismatch`;
   - *direction* — a sign contradicting a requested direction → `direction_mismatch`.

   With `drop_invalid: true` the hard failures (implausible magnitude, unit scale, horizon and
   direction mismatch) are **dropped**; coherence and series-anchor flags stay surface-only
   annotations.
6. **Diagnostics** (`diagnostics.py`) record why each field did or did not ground — the LLM
   emitted nothing, the gate dropped it, validation dropped or flagged it, or the upstream query
   refused — in the response and the trace.

An empty or refused vector is a normal HTTP 200 outcome when evidence is insufficient; the
consumer falls back to its static drivers. Synthesis may escalate on gate loss (LLM Harness §6).

## 10. Factor discovery (`factors/`)

`POST /factors` takes a portfolio or theme and returns ranked controlled-vocabulary `factor_tag`s
with relevance, entity tags and citations (`discover_factors` task, complex tier). A gate keeps
only factors that cite ≥1 dated retrieved chunk.

## 11. Evaluation (`eval/`)

| Surface | What it measures | Entry point |
|---|---|---|
| Retrieval metrics | hit@k, recall@k, MRR over a golden set (each question names the payload field a hit must carry) | `cli eval`, `POST /admin/eval` |
| LLM judge | Aggregate faithfulness and relevancy | `cli eval --judge` |
| Drift gate | Regression vs a stored baseline | `cli eval --fail-on-regression`, admin eval job |
| Prompt × model matrix | Variant × model regression | `cli eval-matrix` |
| Shock accuracy | Synthesized vectors vs **projected** official paths (DFAST/EBA baseline→adverse) — transcription fidelity, not forecast skill | `cli eval --shock` |
| Self-check | Known-answer cases generated from the time-series store + negative controls; judge-free | `POST /admin/selfcheck` |
| Episode backtest | Vectors vs **realized** moves from the DFAST `historic` series, in `grounding_fidelity` and `ex_ante_skill` (retrieval fenced to `release_date` < episode start) modes | `POST /admin/backtest` |

**The two shock-accuracy families are never averaged**, and the two backtest modes have
mode-prefixed summary keys so they cannot merge. A high `ex_ante_skill` abstention rate reflects
corpus depth (most documents are recent), not quality, and is reported beside accuracy. Details:
[guide 04 §5](../guides/04-query-retrieval-eval.md).

Not built: an online shadow-eval loop over sampled production queries.

## 12. Testing

The suite is fully offline: pure logic (RRF, MMR, filters, analysis, metrics, JSON extraction and
schema validation, the prompt registry, the calculator), the corpus loader, the in-memory and
time-series stores, the full retriever and query pipeline with fakes, synthesis and its
validation, and a real Qdrant adapter test in `:memory:` mode (named vectors, server-side RRF,
facet and datetime filters, chunk-id lookup) using the hashing embedder. Spec tests for each
change live under `test/spec/<id>/` and are locked by the TDD pack.
