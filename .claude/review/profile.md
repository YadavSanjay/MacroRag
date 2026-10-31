---
python: "conda run --no-capture-output -n py312 python"
env: {"PYTHONPATH": "{repo}/src"}
gate: ["{python} -m ruff check {changed_py}", "{python} -m mypy src/", "{python} -m pytest -q {tests}"]
tests_map: {"src/ingest/**": ["test/test_ingestion.py", "test/test_scenarios_ingest.py", "test/test_dateextract.py", "test/test_narrative_dating.py", "test/test_house_dating.py", "test/test_curation.py", "test/test_utils_corpus.py"],
  "configs/ingest/**": ["test/test_ingestion.py", "test/test_scenarios_ingest.py", "test/test_config.py", "test/test_curation.py"],
  "src/synthesis/**": ["test/test_synthesis.py", "test/test_shock_eval.py", "test/test_episode_backtest.py", "test/test_factors.py"],
  "src/factors/**": ["test/test_factors.py", "test/test_synthesis.py"],
  "src/eval/**": ["test/test_eval.py", "test/test_eval_matrix.py", "test/test_shock_eval.py", "test/test_episode_backtest.py", "test/test_selfcheck.py", "test/test_quality.py", "test/test_quality_loop.py"],
  "configs/eval/**": ["test/test_eval.py", "test/test_shock_eval.py", "test/test_episode_backtest.py", "test/test_selfcheck.py"],
  "src/llm/**": ["test/test_llm.py", "test/test_llm_cost_logging.py", "test/test_sanitize.py", "test/test_structured_output.py", "test/test_resilience.py", "test/test_escalation.py", "test/test_prompt_registry.py"],
  "prompts/**": ["test/test_prompt_registry.py", "test/test_structured_output.py", "test/test_synthesis.py", "test/test_llm.py"],
  "configs/llm_pricing.yaml": ["test/test_llm_cost_logging.py", "test/test_llm.py"],
  "src/retriever/**": ["test/test_retriever_pipeline.py", "test/test_fusion_mmr.py", "test/test_filters_analysis.py", "test/test_multihop.py", "test/test_p0.py", "test/test_p1.py", "test/test_p2.py"],
  "src/vectorstores/**": ["test/test_embeddings_store.py", "test/test_filters_analysis.py", "test/test_incremental_index.py", "test/test_hydrate.py", "test/test_qdrant_integration.py"],
  "src/embeddings/**": ["test/test_embeddings_store.py", "test/test_remote_inference.py", "test/test_incremental_index.py"],
  "src/pipelines/**": ["test/test_retriever_pipeline.py", "test/test_incremental_index.py", "test/test_hydrate.py", "test/test_synthesis.py", "test/test_manifest_provenance.py", "test/test_quality_loop.py"],
  "src/agent/**": ["test/test_agent.py"],
  "src/api/**": ["test/test_api.py", "test/test_admin.py", "test/test_corpus.py", "test/test_jobs.py", "test/test_hardening.py", "test/test_ui_enablers.py", "test/test_shared_state.py"],
  "src/mcp_server/**": ["test/test_mcp.py", "test/test_mcp_stdio_integration.py"],
  "src/utils/**": ["test/test_manifest_provenance.py", "test/test_objectstore.py", "test/test_config.py", "test/test_metrics.py", "test/test_hardening.py", "test/test_shared_state.py", "test/test_incremental_index.py", "test/test_api.py"],
  "configs/rag.yaml": ["test/test_config.py", "test/test_api.py", "test/test_retriever_pipeline.py", "test/test_synthesis.py"],
  "test/**": ["test/**"]}
gate_env_failures: ["ConnectionRefusedError", "localhost:8799", "localhost:6333", "No module named .(torch|qdrant_client|mcp|boto3|redis)."]
contract_surfaces: ["src/synthesis/vocab.py", "src/synthesis/schema.py", "src/synthesis/gate.py", "src/synthesis/validate.py", "src/api/models.py", "src/api/app.py", "src/mcp_server/server.py",
  "src/utils/manifest.py", "src/utils/schema.py", "src/utils/objectstore.py", "src/utils/config.py", "configs/rag.yaml", "configs/ingest/vocabulary.yaml",
  "configs/eval/golden_stress_backend.yaml", "configs/eval/shock_reference.yaml", "prompts/tasks/synthesize/**", "prompts/system/stress-synthesizer.md",
  "src/ingest/common/models.py", "src/vectorstores/timeseries_store.py", "infra/config/rag.aws.yaml", "docs/guides/05-api-and-integration.md"]
hotspots: ["src/utils/**", "src/llm/usage.py", "src/llm/pricing.py", "src/llm/base.py", "src/embeddings/base.py", "src/ingest/common/**", "src/synthesis/vocab.py"]
criticality: {"outputs": {
    "shock_vector": ["src/synthesis/{synthesizer,gate,validate,schema,vocab}.py"],
    "world_view_id": ["src/utils/manifest.py"],
    "answers": ["src/llm/generate.py", "src/llm/verify.py", "src/retriever/retriever.py"],
    "eval_evidence": ["src/eval/{shock_eval,episodes,drift}.py"]},
  "data_edges": ["src/ingest/common/{models,chunking,dateextract,handler_base}.py -> src/pipelines/index_pipeline.py",
    "src/ingest/common/models.py -> src/vectorstores/timeseries_store.py",
    "src/vectorstores/timeseries_store.py -> src/pipelines/hydrate.py",
    "src/vectorstores/timeseries_store.py -> src/eval/{episodes,selfcheck}.py",
    "src/pipelines/index_pipeline.py -> src/utils/manifest.py",
    "configs/rag.yaml -> src/utils/config.py", "configs/ingest/vocabulary.yaml -> src/ingest/common/config.py",
    "prompts/tasks/{synthesize,answer,verify,plan}/** -> src/llm/registry.py",
    "prompts/system/{stress-synthesizer,macro-analyst,fact-checker,strict-json}.md -> src/llm/registry.py"],
  "non_value": ["src/utils/{logging,metrics,observability,trace,alerting}.py", "src/llm/{usage,pricing}.py",
    "src/synthesis/diagnostics.py", "src/api/jobs.py", "src/api/jobstore.py"],
  "pins": {"trunk": ["src/synthesis/vocab.py", "src/synthesis/gate.py", "src/utils/manifest.py",
    "src/pipelines/query_pipeline.py", "prompts/tasks/synthesize/base.md"]}}
---
# MacroRAG — review profile (review-core pack)

The frontmatter drives the review-core scripts (gate, test scoping, blast map). This prose is
passed to reviewers verbatim. Layout facts: `src/` is the import root (`import synthesis`, not
`src.synthesis`); tests live in flat `test/`; prompts live at the repo-root `prompts/`.

## Coding standard

Review touched code against the Google Python Style Guide with the house deviations in the global
`rules/python.md` (refactored units in full, small edits on the changed lines only), plus
`.claude/rules/rag.md`. Tool-checked rules (ruff, mypy, `tools/standards/check_standards.py`) are
not review findings unless the tools were bypassed or their config loosened.

## Data/runtime availability

- The test suite is fully faked (hashing embedder, in-memory store, `FakeLLM`, `InMemory`
  object store, `fakeredis`); it needs **no** `data/`, network, torch, Qdrant or RagInference.
  `test_qdrant_integration.py` and `test_mcp_stdio_integration.py` use `importorskip` and may
  skip. A skip is not a pass: list it under **Skipped**.
- `data/` (raw, processed, vectorstore, traces) is gitignored and holds the real corpus and the
  SQLite time-series store. Anything that needs it, or live Qdrant (`:6333`), RagInference
  (`:8799`), OpenAI/Anthropic, FRED/BEA/BLS/EIA, or `scripts/run_questions_live.py`, is
  unverifiable in review. Record it as an environment gap, never as caused by the change.
  Real-corpus claims (eval hit@k, episode backtest skill, shock accuracy) cannot be proven here.
- Never call live services, ingest sources, the index CLI (an ~80 min CPU re-embed) or an LLM
  during a review. The bare `python` on PATH lacks dependencies; always use the profile command.
- The conftest teardown prints "Running teardown with pytest sessionfinish..." after the run;
  trust the exit code.
- `mypy src/` must stay at **zero errors** (`mypy.ini`). `ruff` is critical-only (`F`, `E9`):
  a style complaint is not a finding.

## Contract-surface notes (beyond the globs)

- **Shock-vector synthesis contract** (consumed by the downstream AITrade stress engine, which
  is the `faultline` repo): the 19-field vocab, units and sign conventions in
  `src/synthesis/vocab.py` are authoritative, mirrored in `ShockField.to_dict()`
  (`value, unit, chunk_ids, grounding_mode, rationale?, validation?`), `synthesis_to_dict`
  (`shock_vector`, `world_view_id`, `synthesis_diagnostics`), the discovery endpoint
  (`schema_dict`), the prompt `field_reference()` and the table in guide 05 §5. Renaming a
  field, changing a unit or flipping a sign is a CONTRACT break even if tests are updated.
- **`world_view_id`** is content-addressed: `macro-<YYYY-MM-DD>-<12 hex>` from
  `corpus_digest` over sorted `(chunk_id, content_hash)`. Legacy manifests keep a date-only
  form on purpose. The id is stamped on every shock vector and appended to
  `manifest_history.jsonl`. This is an audit trail, not a snapshot store; it is documented
  that a superseded id cannot be re-synthesized against (Production Hardening §F).
- **Manifest** (`utils/manifest.py`): `embedding_signature` decides when a full re-index is
  required; `prompt_hash` covers all of `prompts/**`.
- **Prompt registry**: `prompts/tasks/<task>/{base.md,base.meta.yaml,schema.json}` and
  `prompts/system/*.md`; exactly one LLM call is wrapped per `prompt_context`.
- **API guardrail codes** (§L): 409 job lock, 402 cost cap (`BudgetExceeded`), 422 input
  bounds, 503 `api.max_concurrency`. REST (`src/api`) and MCP (`src/mcp_server`) mirror each
  other; CLI/REST/MCP docs live in guide 05 only.
- **`.claude/` is partly tracked:** `review/` (minus `out/`), `rules/`, `hooks/` and
  `settings.json` are versioned; `settings.local.json`, `state/` and the rest stay local.

## Terminology (do not flag)

- **AITrade = the downstream stress engine = the `faultline` repo** (renamed from
  QuantLibPricer). Code comments still say "AITrade" while docs say faultline; do not flag the mismatch.
- **ARag = XbrlRag** (sibling service sharing RagInference); `ARag` in comments/docs is the
  same service.
- **`principal` vs `tenant_id`** are deliberately different (auth/cost identity vs a dormant
  isolation hook). Deprecated aliases `cost.per_tenant_daily_usd` and `--tenant` stay.
- **`grounding_fidelity` vs `ex_ante_skill`** and **transcription fidelity (`shock_eval`)**
  vs realized-move evidence are separately named on purpose; flag only code that merges them.
- Tier names (`tier1_fed` ... `tier7_scenarios`), buckets A/B/C, and the "spec §N"
  references (MacroRAG_synthesis_endpoint_spec) are project vocabulary.

## Exclusions (project-wide non-findings)

- Generous plausibility bands in `MAGNITUDE_BOUNDS`: they are hallucination ceilings, not
  calibration. Flag a number only when it is wrong for its unit.
- Annotate-don't-drop in `validate_shock_vector` (coherence/anchor flags are surface-only;
  only `implausible_magnitude` is hard and only with `drop_invalid`).
- Fail-soft in `complete_json` (validate, one repair retry, best-effort parse); schemas are
  permissive on purpose.
- `inference.fallback_local: true` in local config; forced off in the no-torch prod image.
- Design docs under `docs/design/` are dated intent; when code diverges the code wins.
  Flag only code that contradicts `CLAUDE.md` or guide 05.
- Time-decay ranking is off by default; escalation is off by default.
