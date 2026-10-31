# MacroRAG — LLM Harness

The provider-portable LLM layer under every generation step: one narrow client contract, a
versioned prompt registry, schema-validated structured output, provider-aware model tiers,
quality-signal model escalation, and a prompt × model regression matrix. Companion to the
[pipeline design](Macro_RAG_pipeline_design.md), whose generation, verify and synthesis
stages run on it.

Working notes for editing the code: [src/llm/CLAUDE.md](../../src/llm/CLAUDE.md). Tier and
provider configuration for operators: [guide 04 §4](../guides/04-query-retrieval-eval.md).
Resilience, schema-health observability and cost caps:
[Production Hardening §B, §H, §K](Macro_RAG_Production_Hardening.md).

---

## 1. Design principles

- **Adopt the patterns, not a framework.** Schema-validated output, versioned prompts, eval
  gating, fallback chains and tracing are all here without LangChain/LangGraph; the layer is
  small, fully faked in tests, and keeps the existing trace and usage plumbing.
- **Provider idioms live inside the client**, never in prompts. Every prompt is plain,
  provider-agnostic text — no `response_format`, no tool schemas, no few-shot role turns.
- **Structured output is validated, not provider-enforced.** Prompts ask for JSON; the harness
  validates against a JSON Schema and repairs once. This works identically on every provider
  and on the fake client.
- **Fail-soft stays.** Every task keeps a safe fallback (plan → DIRECT, verify → keep the claim,
  synthesize → suppress the field). Validation makes failures observable; it does not turn them
  into exceptions.
- **The agent planner is deterministic Python**, not provider tool-use: its plan call gets
  schema validation, and orchestration stays auditable.

## 2. Client contract

`LLMClient.complete(*, system, user, temperature=None, model=None, purpose=None) -> str` is the
only abstract method (`src/llm/base.py`): one system string, one user string. `model` overrides
the tier per call; `purpose` labels the call in the usage log.

- `build_llm(settings)` picks `OpenAIClient` or `AnthropicClient` by `llm.provider`, with
  deferred SDK imports. `FakeLLM` (scripted) implements the same contract for tests.
- **OpenAI** uses `max_completion_tokens`. **Anthropic** sends the system prompt top-level with
  an `ephemeral` `cache_control` breakpoint, uses `max_tokens`, and omits `temperature` for
  models that reject it.
- **Per-tier options travel by context, not by parameter.** A call site wraps its call in
  `usage.tier_context(<tier>)`; the client looks up that tier's `max_tokens`, `effort` and
  `timeout_s` in the active profile (`tier_options_for`, §6) and sends them: OpenAI as
  `max_completion_tokens` / `reasoning_effort` / per-request `timeout`, Anthropic as
  `max_tokens` / `output_config.effort` / `timeout`. An unset option keeps the global. The
  signature of `complete` stays the same, so test doubles need no change. With a reasoning
  effort above `none`, the OpenAI client omits `temperature` (gpt-5.4 rejects it then).
  Reasoning and thinking tokens count against the output cap on both providers.
- Every provider call goes through `llm/resilience.py` (retry with backoff + circuit breaker)
  and ends with `record_call(...)`, which records the normalized finish reason (`stop` /
  `length` / `refusal`), the reasoning tokens (OpenAI reports them; Anthropic does not) and the
  model the response names (`served_model`; a WARNING when it is not the requested model or a
  dated snapshot of it).
- **Refusals fail soft.** A refused reply is billed and recorded, then raises `LLMRefusalError`
  (model, category); `complete_json` catches it (§5), so callers see today's empty result.
- **No model fallback.** A refused or failed request is never re-run on another model: retries
  repeat the same model, and Anthropic's server-side `fallbacks` parameter is not sent. The model
  that answers is the one the tier names, so a model changes only through config and an eval run
  (§6, §7), never at request time.

## 3. Tasks under management

| Task | System role | Tier | Output (schema primary key) |
|---|---|---|---|
| `plan` (route, paraphrase, decompose, HyDE) | `strict-json` | plan | `route`, `paraphrase`, `subqueries[≤5]`, `hyde[≤2]` |
| `answer` | `macro-analyst` | answer or complex, by route | `summary`, `details[{text, chunk_ids}]`, `unanswered` |
| `verify` (faithfulness) | `fact-checker` | plan | `results[{index, supported, reason}]` |
| `judge` (eval) | `eval-judge` | plan | `faithfulness`, `relevancy` |
| `plan_execute` (agent plan) | `strict-json` | plan | `quantitative`, `data`, `compute`, `retrieve` |
| `next_hop` (multi-hop) | `strict-json` | plan | `done`, `next_query` |
| `discover_factors` | `macro-risk-analyst` | complex | `factors[{factor_tag, relevance, entity_tags, chunk_ids}]` |
| `synthesize` (shock vector) | `stress-synthesizer` | complex | `shock_vector[FIELD]{value, unit, chunk_ids, grounding_mode, …}` |

`synthesize` is the most behaviour-sensitive prompt (unit and sign conventions, the
peak-not-endpoint rule, data-to-field mappings, `source_horizon_days`).

## 4. Prompt registry

```
prompts/
  system/<role>.md                         # system strings
  tasks/<task>/
    base.md                                # provider/model-agnostic prompt (always present)
    base.meta.yaml                         # task, variant_id, version, status, …
    schema.json                            # JSON Schema for the structured output
    variants/<provider>.<series>.v<N>.md   # optional model-series variant (+ .meta.yaml)
```

No variants exist today; every task resolves to `base`. A variant is added only when the matrix
(§7) shows a model-specific regression — no code change is needed to add one.

**Resolution** (`get_registry().resolve(task, provider=, model=, pin=)`): an explicit `pin`
wins; then the latest active variant matching `(provider, model series)`; then `base`. Pins are
set programmatically (`set_pins`) — the eval matrix uses them to run an old prompt against a new
model. There is no config key for pins.

**Provenance.** `ResolvedPrompt` carries `variant_id` and `content_hash`. Wrapping the call in
`prompt_context(variant_id, content_hash)` stamps both onto the `CallRecord` and the query trace
(`prompt_variant`, `prompt_hash`), so every generation and every eval row is attributable to an
exact prompt. The answer cache key includes the prompt hash and model tier.

`render()` is deliberately not `str.format`, so JSON braces in prompts survive. `prompts.py`
(`load_prompt` / `render`) is a thin shim over the registry.

## 5. Structured output

```
complete_json(system, user, schema)
  → complete → extract_json (tolerant scraper)
      refused   → return {}; no repair call
  → validate against schema
      ok        → return
      invalid   → one repair call (purpose "<task>:repair", validation errors appended)
                  → validate again → return best-effort parse either way
                    (a refused repair returns the first parse)
  → record_schema_result(task, model, ok | repaired | failed | refused)
    + truncated, once, when any reply ended at the output cap
```

Schemas are permissive — they require only the primary key — so they catch gross drift without
tripping fail-soft paths. An optional field that its consumer treats as absent when null accepts
`null` (the plan task's `paraphrase`, `subqueries` and `hyde`): models routinely answer null
there, and a type-only rejection would cost a repair call for nothing. A cut-off reply still gets its repair call: a retry can fit if the model
writes less, while the lasting fix is the tier's `max_tokens`. A refusal gets none, because the
same request would be refused again. Outcomes feed `GET /admin/schema-health`, a Prometheus
counter, and the matrix's per-cell `repair_rate`. `total` counts `ok + repaired + failed +
refused`; `truncated` marks calls already counted, so it is reported beside `total`, not in it;
`repair_rate` is `(repaired + failed) / total`. `schema_errors()` degrades gracefully if
`jsonschema` is absent.

## 6. Model tiers and escalation

**Tiers** come from a per-provider profile in `configs/rag.yaml` (`llm.profiles.<provider>`:
`plan`, `answer`, `complex`, optional `default` and `escalation`). Switching `llm.provider`
remaps every tier; a flat `plan_model` / `answer_model` / `complex_model` key overrides one tier
for the active provider. A second set, `llm.replacement_profiles`, holds the models that replace
deprecated ones; the switch `llm.use_replacement_models` (off by default; env
`MACRORAG_USE_REPLACEMENT_MODELS` overrides it, an unknown value raises) takes the active
provider's tiers from it, so an eval run can compare the two sets without a config edit. Routes in `llm.complex_routes` generate on `complex`. Call sites read
`settings.llm.{plan,answer,complex}_model` / `escalation_model`; model ids are never literals.
The `plan` tier must be capable — it drives routing, decomposition and the time-series lookup,
so a weak planner degrades retrieval.

**Tier options.** A profile may also carry `tier_options`: per tier, an optional `max_tokens`,
`effort` and `timeout_s`. The options of the profile that supplied the models apply (the
replacement profile when the switch selects it). Each call runs under the tier of the setting
that supplied its model: `plan` for planning, `plan_execute`, `multi_hop` and `verify`; `answer`
or `complex` for `generate` by route; `complex` for synthesis and factor discovery; an escalated
re-run under `complex` or `escalation`, whichever model escalation picked. The eval judge runs
under none. Efforts are checked against the provider's set when settings load (Anthropic `low`
… `max`; OpenAI `none` … `max`); whether a given model takes a level is for the eval run (§7).
The committed config sets no tier options: their values come from an eval comparison of
cut-off rate, repair rate, reasoning tokens and cost.

**Escalation** (`llm/escalation.py`, orchestration in `query_pipeline._maybe_escalate_prose` /
`_maybe_escalate_synth`) re-runs a deteriorated pass on the next tier up (answer → complex →
escalation) and keeps the result only if it is measurably better. **Off by default.**

| Path | Signals | Skipped when |
|---|---|---|
| Prose | `empty_answer`, `all_unsupported`, `verifier_drop` (unsupported share ≥ `verifier_drop_ratio`) | The answer is a relevance-floor refusal (no evidence; a stronger model can't help) |
| Synthesis | `llm_empty`, `all_dropped`, `gate_loss` (gated share ≥ `gate_loss_ratio`) | Synthesis was skipped for lack of evidence |

Guardrails: `max_per_request` re-runs, and `cost_budget_usd` — no escalation once the request's
spend reaches it. Every upgrade is recorded in the trace's `escalations[]` array and tagged
`purpose=…:escalated` in the usage log, so the admin `by_model` roll-up shows how often each
task escalates — the evidence for promoting a task's default tier. With the default profiles only
simple-route prose has a higher tier; add an `escalation` model to a profile to enable the rest.
Escalation stays within the active provider. Any model escalation can reach needs a row in
`configs/llm_pricing.yaml` (longest-prefix match); settings refuse to load without one (§9).

## 7. Prompt × model regression matrix

`pipelines.cli eval-matrix --task {answer,synthesize} [--models …] [--variants …]
[--golden …] [--judge-model …] [--save-baseline] [--fail-on-regression]` (`src/eval/matrix.py`)
runs `{variant} × {model} × {golden case}`; it refuses to start while any `--models` id or the
judge model is unpriced (§9). Each cell validates output against the task schema
and scores it with the task's eval metrics (judge scores, unsupported-claim and dropped-field
counts) plus the cell's `repair_rate`. Baselines are saved per
`(variant_id, model)`; `--fail-on-regression` exits non-zero beyond tolerance, so a prompt or
model change ships with an eval diff.

## 8. Caching boundaries

| Layer | Caches | Key | Where |
|---|---|---|---|
| Provider prompt cache | Token prefix | Byte-exact prefix | Inside the client (Anthropic breakpoint on the system prompt; OpenAI automatic). **Inactive with today's prompts** — see below |
| Per-call response cache | — | — | Not built (see below) |
| Query answer cache | The whole answer | L1 exact query (on by default); L2 semantic, cosine ≥ `semantic_cache.threshold` (0.97, off by default) | `query_pipeline`; keys include filter scope, corpus, prompt hash, the plan / answer / complex models, `llm.max_tokens` and those tiers' options (`cache_version`) |

**Decision that matters:** semantic (fuzzy) matching applies only to whole user questions. A
near-identical `plan`, `verify` or `synthesize` prompt can need a materially different output, so
structured sub-calls are never cached fuzzily. An exact-match per-call cache keyed on
`(content_hash, model, rendered user)` would be safe but is not built: the answer cache already
short-circuits repeat queries. Every key includes the model, so an escalated call can never be
served a cheaper model's cached result.

**Provider prompt caching does not engage.** A provider caches only a stable prefix of at least
a model-dependent minimum: 512 tokens on the Claude 5.5 models, 1024 on Sonnet 4.6 / Opus 4.8,
4096 on Haiku 4.5, and 1024 for OpenAI's automatic caching. MacroRAG's system prompts are 13–27
tokens, and each task's stable prefix (system prompt plus the user template up to its first
`{slot}`) is 92–459 tokens (o200k count; `synthesize` is the largest), because the question and
the retrieved context follow a short instruction block. So nothing is written to or read from a
provider cache (the 2026-10-21 pilot saw zero cached tokens on every model set), and the
Anthropic breakpoint costs nothing either: below the minimum no cache write is billed. The one
candidate is `synthesize` (1,564-token template): moving its variable slots to the end would give
a ~1.5K-token prefix that caches on the newer Claude models, which pays only when synthesis runs
repeatedly within the 5-minute cache lifetime (e.g. a faultline batch).

## 9. Accounting

`record_call` logs one line per call; inside `track_usage(label)` it also folds into an aggregate
and a `CallRecord` (provider, model, purpose, tokens, cost, latency, prompt variant and hash).
`usage_snapshot()` serializes it into the query trace, and `/admin/metrics` rolls traces up
`by_model`. The same calls feed the Prometheus LLM counters and the cost guard
([Production Hardening §J, §K](Macro_RAG_Production_Hardening.md)).

`pricing.call_cost` prices each call from `configs/llm_pricing.yaml` (USD per 1M tokens):
uncached prompt tokens at `input`, cache reads at `cache_read`, cache writes at `cache_write`
(Anthropic bills 5-minute writes at 1.25 × input; each defaults to `input`), output at `output`.
The card is validated when it loads, so a mistyped key raises. A row may set
`max_priced_prompt_tokens`: a longer prompt logs a warning because the provider's long-prompt
rate is higher than the card's (Haiku 5.5 above 100K).

**Completeness is enforced at load, not at call time.** An unpriced call would add $0 to the
request total and so slip past the cost guard. So settings loaded from `configs/rag.yaml`
(`load_settings_from`) raise unless every configured model has a row: every tier of every
provider's `profiles` and `replacement_profiles` entry plus the resolved tier fields
(`configured_models`), so switching `llm.provider` or `llm.use_replacement_models` cannot reach an
unpriced model. `eval-matrix` checks its ids the same way (§7). A call that still reaches an
unpriced model logs a one-time warning, carries `cost_usd: null`, and marks the answer's
`usage.cost_complete` false.
