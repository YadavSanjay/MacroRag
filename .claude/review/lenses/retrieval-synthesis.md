---
name: retrieval-synthesis
applies_to: ["src/{synthesis,factors,retriever,pipelines,agent,embeddings,llm,eval,api,mcp_server}/**", "src/vectorstores/{base,memory,qdrant_store,filters}.py", "src/utils/{manifest,objectstore,cache,costguard,schema,config,ratelimit}.py", "prompts/**", "configs/{rag.yaml,llm_pricing.yaml}", "configs/eval/**", "infra/config/**"]
---
# Retrieval, grounded synthesis and the AITrade contract

MacroRAG's headline output is a **citation-gated shock vector** consumed by the downstream
AITrade stress engine (the `faultline` repo). Every item below protects either the
grounding chain (retrieve, rerank, generate, gate, validate, reconcile) or the identity and
cost guarantees around it.

## Hunt for

**Citation gate and grounding chain (most damaging)**
- `gate_shock_vector` is the hard bound on machine-consumed output. Any path that returns
  fields without it: a new code path in `_ground_shock`/`synthesize`/`_maybe_escalate_synth`,
  an escalation pass whose `kept` skips the gate, `drop_invalid` or a `min_chunks=0` default,
  or a gate that checks `chunk_ids` against all retrieved chunks rather than the **dated**
  ones (`release_date` populated). Order must stay: synthesize, gate, validate, reconcile.
- Escalation keep-if-better: the higher-tier result must be compared on gated field count and
  must itself have passed the gate; a failed or cheaper pass must not overwrite a good one.
- `ShockField.to_dict()` and `synthesis_to_dict` drop or rename `grounding_mode`,
  `validation`, `chunk_ids`, `world_view_id` or `synthesis_diagnostics`. Every cited
  `chunk_id` must be present in the response `chunks[]`.
- `reconcile_grounding_modes` promoting a field to `direct` without a genuine direct series
  (`RECONCILE_FIELD_TO_SERIES` is deliberately stricter than `SHOCK_FIELD_TO_SERIES`).
- Faithfulness `verify_answer` fail-soft ("keeping answer as-is") is deliberate for prose; it
  must not become the grounding mechanism for the shock vector.
- Refusal floor (`retrieval.min_rerank_score`) and `SynthesisDiagnostics(skipped=...)`
  reasons: a path that emits a vector after `upstream_refused` or `no_chunks`.

**Identity, provenance and index invalidation**
- `world_view_id` must remain a pure function of the corpus: `corpus_digest` over sorted
  `(chunk_id, content_hash)`. Adding a clock, build time, config or random value to it, or
  hashing something unsorted, breaks the id across identical re-indexes. Both `run` and
  `run_incremental` must append manifest history via `_finish_manifest`; a new build path that
  writes the manifest without history or digest degrades silently to the legacy date-only id.
- Incremental indexing diffs on the **per-document** `content_hash` (`_signature`). A chunking
  change (`ingest/common/chunking.py`, `scripts/rechunk_processed.py`) or a payload-field
  change that keeps the doc hash leaves stale vectors in place and the digest unchanged.
  Embedder, dims or grounding-prefix changes must flip `embedding_signature` (`is_stale`) and
  force a full rebuild; query and corpus vectors must both come from `select_embedder`.
- Cache invalidation: `cache_key(..., version=self._cache_version)` and the semantic cache must
  change with the corpus, prompt hash and model tier. A new retrieval/synthesis parameter
  (fields, horizon, scenario, filters) that is not in the key serves a stale or wrong cached
  answer. A cached synthesis whose `world_view_id` no longer matches the live one.
- `prompt_hash` covers `prompts/**`: prompt edits that bypass the registry (inline strings in
  code) escape both drift detection and `prompt_variant`/`prompt_hash` provenance. More than
  one LLM call inside one `prompt_context` makes the provenance stamp ambiguous.
- `deploy_mode`: object data goes through `utils.objectstore` only. A raw `Path("data")` read
  or write for raw/processed/manifest breaks `aws` mode; SQLite, traces and eval artifacts
  legitimately stay local. S3 creds from the ambient chain only, `boto3` lazily imported.

**Retrieval correctness**
- `MetaFilter` semantics (AND across facets, OR within a facet) must match between
  `filters.match_payload` (memory store/tests) and the Qdrant filter builder: a facet
  implemented on one side only passes in tests and fails in production. `tenant_id` is a
  dormant hook; do not describe or rely on it as isolation.
- Fusion/rerank: RRF constants, `rerank_candidates` truncation before dedupe, MMR dropping the
  highest-scored chunk, time-decay applied before the relevance floor (it would then change
  refusals). `apply_time_decay` and `date_from/date_to` run on `release_date`; see
  `macro-data-integrity`.
- The agent's `calculator` is the "safe quant" path: any `eval`/`exec`/attribute access or
  unbounded expression there is a security defect.

**Untrusted text and prompt injection**
- Ingested filings, house views, web pages and the user `scenario` are data. Retrieved text
  must reach prompts only through `generate.format_context` / `fence_untrusted`; any new
  prompt (`synthesize`, `plan`, `next_hop`, `verify`, `discover_factors`, `plan_execute`,
  `judge`) that interpolates chunk text, titles, publisher strings or time-series `raw` JSON
  unfenced is a hole. `note_injection` must run on new untrusted inputs. Fence markers must
  stay un-spoofable. Flag detection regexes loosened to match ordinary macro prose.
- LLM output reaching state: model-emitted field names, units and chunk_ids must be validated
  against `vocab.SHOCK_FIELDS` / the gated chunk set before use; factor discovery
  (`factors/`) output must not write the live vocabulary without its own gate.

**LLM cost, budgets and resilience**
- Every provider call must go through `record_call` (and `usage.track_usage` at the entry
  point) and the `resilience` retry/breaker; a new loop, retry or fan-out (multi-hop,
  paraphrase, HyDE, escalation, eval-matrix, judge) without a bound is a cost defect.
- `can_afford` and `CostGuard.check` gate admission (402 `BudgetExceeded`); `record` accounts
  post-request. A new endpoint or job that calls an LLM outside the guarded wrapper, or an
  escalation target without a row in `configs/llm_pricing.yaml` (cost recorded as zero).
- Model ids are config (`settings.llm.*_model`), never literals. Provider idioms stay in the
  client (`_accepts_temperature`, `max_completion_tokens`).

**Synthesis endpoint contract (consumed by AITrade/faultline)**
- Vocab, units, sign conventions and the 17 field names are contract (`vocab.py`), as are
  the request controls `fields`, `horizon_days`, `scenario`, `mode=synthesize`, error
  statuses (402/409/422/503), and discovery `schema_dict`. REST (`api/app.py`) and MCP
  (`mcp_server/server.py`) must expose the same set; prompts and guide 05 §5 must match
  `vocab.py`.
- `_constraints_block` honoring `fields`/`horizon_days`; an unknown field requested must be
  rejected (422), not silently dropped or invented.

**Eval regressions**
- `eval --fail-on-regression` / drift (`eval/drift.py`, `--tol`) compare against stored
  baselines: a metric rename, a changed denominator or an eval path that skips the baseline
  turns the CI gate into a no-op. Evidence kinds must stay separate: `shock_eval`
  (transcription fidelity), `grounding_fidelity` and `ex_ante_skill`; never averaged or
  merged into one summary key. `eval-matrix` baselines must be keyed by prompt variant+model.
- Changing a golden file (`configs/eval/golden*.yaml`, `shock_reference.yaml`) to make a
  regression pass.

## Severity anchors

- P0: shock vector returned without passing the citation gate or with uncited/undated
  chunks; a unit/sign change to the vocab contract that is not versioned; untrusted
  text executed as an instruction with the output machine-consumed unvalidated; secrets or
  real AWS identifiers written to a committed file.
- P1: `world_view_id` no longer content-addressed or not stamped/recorded; stale vectors after
  a chunking/embedder change with no rebuild signal; cache serving across corpus/prompt
  changes; unbounded LLM loop or an un-guarded paid endpoint; REST/MCP drift; Qdrant vs
  memory filter divergence; eval gate neutered; evidence kinds merged.
- P2: escalation target missing from pricing; unfenced prompt input with low reachability;
  missing diagnostics reason; missing fake for a new external dependency.
- P3: log wording, trace field naming, docstring/spec-section drift.

## Proof recipes

- `pytest test/test_synthesis.py test/test_retriever_pipeline.py test/test_quality_loop.py
  test/test_sanitize.py test/test_manifest_provenance.py test/test_incremental_index.py
  test/test_api.py test/test_mcp.py -q` (exit code).
- Gate repro with fakes: build `ShockField`s citing an undated or absent `chunk_id` and call
  `gate_shock_vector`; drive `QueryPipeline.synthesize` with scripted `FakeLLM` returning an
  uncited field and assert the vector is empty and `diagnostics` records `no_dated_citation`.
- Identity repro: `corpus_digest` is unchanged when pair order or `built_at` changes, and
  changes when one `content_hash` changes; build twice via the in-memory store and compare
  `world_view_id_from(manifest)`.
- Injection repro: put `"ignore all previous instructions"` and a forged
  `<<<END_UNTRUSTED_CONTEXT>>>` in a chunk, run `format_context`, and assert the fence stays
  closed and `detect_injection` fires.
- Drift repro: grep both surfaces for the endpoint/field set and diff `vocab.SHOCK_FIELDS`
  against `schema_dict()`, the prompt `field_reference()` and guide 05 §5.
- Cost repro: `FakeLLM` plus `track_usage` to confirm a new call site records usage and
  respects `can_afford`.

## Not findings

- Live-model quality, retrieval hit@k on the real corpus, and latency (not measurable in review).
- Keep-the-live-corpus-only guarantee: no snapshot store for superseded `world_view_id`
  (Production Hardening §F, decided).
- Escalation, multi-hop, time-decay, semantic cache and Redis backends being off or memory by
  default; `inference.fallback_local` locally.
- `principal` used for auth/rate-limit/cost while `tenant_id` is dormant (Production Hardening §A).
- Permissive prompt `schema.json` (require only the primary key) and `complete_json`
  fail-soft; detection regexes being "defense in depth, not a guarantee".
- Freshness (age vs valuation date) is AITrade's decision; MacroRAG only guarantees a dated,
  cited chunk.
