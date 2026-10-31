# src/llm — LLM harness

Provider-portable LLM layer: one narrow client contract, a versioned prompt registry,
schema-validated structured output, tiered/provider-aware model selection, quality-signal
escalation, and per-call cost/provenance accounting. Design rationale:
[docs/design/Macro_RAG_LLM_Harness.md](../../docs/design/Macro_RAG_LLM_Harness.md).

## The client contract (`base.py`)

- `LLMClient.complete(*, system, user, temperature=None, model=None, purpose=None) -> str` —
  the ONLY abstract method. One system + one user string (no message list, no tool calls).
  `model` overrides the tier per call; `purpose` is a free-text label for the usage log.
- `complete_json(*, system, user, schema=None, model, purpose)` — concrete helper: parse
  with the tolerant `extract_json`, and when `schema` is given, **validate → one repair
  retry (purpose `:repair`) → fall back to best-effort parse**. Fail-soft is preserved; a
  silent `{}` becomes an observable, retried event. `schema_errors()` degrades gracefully
  if `jsonschema` is missing. `schema_health.py` tracks the schema outcome
  (ok/repaired/failed/refused, plus `truncated` when a reply hit the cap) per task, feeding
  `GET /admin/schema-health` and the eval-matrix `repair_rate` (Production Hardening §B).
  A provider refusal raises `LLMRefusalError` from `complete`; `complete_json` catches it,
  skips the repair and returns `{}`. It spots a cut-off through the client's
  `last_finish_reason`, which every `complete` must set (normalized via
  `normalize_finish_reason`).
- **Provider calls are wrapped by `resilience.py`** (`call_with_retry` + `CircuitBreaker`,
  config `llm.resilience.*`, §H): transient provider errors retry with backoff, and a tripped
  breaker fails fast instead of hammering a down provider. Keep provider `complete` bodies the
  raw call — the retry/breaker policy lives in `resilience`, not in each client.
- **Every provider client MUST call `record_call(...)` after a completion** (see `usage.py`)
  and be fully faked in tests. Add a provider by implementing `complete` + `build_llm`
  wiring; never bypass the contract.

## Providers (`openai_client.py`, `anthropic_client.py`, `__init__.build_llm`)

- `build_llm(settings)` selects the client purely by `settings.provider` (deferred imports).
- Provider idioms stay **inside** the client, never in prompts: OpenAI uses
  `max_completion_tokens` and omits `temperature` for the gpt-6 family
  (`accepts_temperature`); Anthropic uses `max_tokens` + an `ephemeral` `cache_control`
  breakpoint on the system prefix, and **omits `temperature`** for models that 400 on it
  (`_accepts_temperature`: Opus 4.7 and later and the whole Claude 5 family). No hardcoded
  model ids — they resolve from the provider profile (`utils/config.py`), or from
  `replacement_profiles` when `llm.use_replacement_models` (env
  `MACRORAG_USE_REPLACEMENT_MODELS`) is on.
- **Per-tier options come from context.** A call site wraps its call in
  `usage.tier_context("<tier>")`; the client reads `tier_options_for(settings, current_tier())`
  and applies `max_tokens`, `effort` (OpenAI `reasoning_effort`, which also drops
  `temperature` above `none`; Anthropic `output_config.effort`) and a per-request `timeout`.
  A new call site must set the tier of the setting that supplied its model, or it runs on the
  globals. Pass `finish_reason`, `reasoning_tokens` and `served_model` to `record_call`.

## Prompt registry (`registry.py`, `prompts.py`)

- Prompts live in `prompts/tasks/<task>/base.md` (+ `base.meta.yaml`, `schema.json`) and
  `prompts/system/<role>.md` at the **repo root** — NOT in code, NOT in an old `prompt/` dir.
- `get_registry().resolve(task, provider=, model=, pin=) -> ResolvedPrompt` returns the
  system string, the user template, `schema`, and provenance (`variant_id` + `content_hash`).
  Resolution: `pin` › `(provider, model_series, latest active)` › `base`. `set_pins()` drives
  the variant axis (the eval matrix pins variants programmatically; there is no config key).
- `prompts.py` (`render`/`load_prompt`) is a **thin shim** over the registry — prefer
  `resolve()` in new code.
- **Adding a task/prompt:** create `prompts/tasks/<task>/{base.md, base.meta.yaml, schema.json}`
  (+ a system role file if new), then at the call site: `rp = get_registry().resolve("<task>", model=m)`
  → `with prompt_context(rp.variant_id, rp.content_hash): llm.complete_json(system=rp.system,
  user=rp.render(...), schema=rp.schema, purpose="<task>")`. Wrap **exactly one** call in
  `prompt_context` so the provenance stamp is unambiguous. Schemas are permissive (require
  only the primary key) so they catch gross drift without tripping fail-soft paths.
- **Adding a model-series variant** (no code change): drop
  `prompts/tasks/<task>/variants/<provider>.<series>.v<N>.md` + `.meta.yaml`; the resolver
  picks it for matching `provider`+`model`.

## Task modules

`generate.py` (answer), `verify.py` (faithfulness), `understanding.py` (route/plan),
`escalation.py` (policy). Each resolves its prompt via the registry and is provider-agnostic.
`generate_answer` / `synthesize_shock_vector` (in `synthesis/`) take a `purpose` param so an
escalated re-run tags as `:escalated`. `sanitize.py` provides prompt-injection defense —
`generate.format_context` isolates retrieved-chunk text from instructions and runs injection
detection so corpus content can't hijack the prompt (Production Hardening §D).

## Escalation (`escalation.py`)

Pure decision logic — `EscalationPolicy`: `next_model` ladder (answer‹complex‹escalation),
`can_afford` (per-request cap + live cost budget via `current_usage()`), `prose_signal` /
`synth_signal`. The re-run + keep-if-better orchestration lives in `pipelines/query_pipeline.py`
(`_maybe_escalate_prose` / `_maybe_escalate_synth`). **Off by default**; with default profiles
only simple-route prose has a higher tier.

## Accounting & provenance (`usage.py`, `pricing.py`)

- `record_call` logs one line per call and, inside a `track_usage(label)` context, folds it
  into an aggregate + `CallRecord` (model, provider, purpose, tokens, cost, latency, and —
  via `prompt_context` — `prompt_variant`/`prompt_hash`). `usage_snapshot()` serialises it
  into the query trace. Pipeline entry points wrap in `track_usage`.
- `pricing.py` prices per model via `configs/llm_pricing.yaml` (longest-prefix match) with
  `call_cost` (cache reads and writes at their own rates). A provider client passes cached and
  cache-written tokens to `record_call`. Every model id `rag.yaml` can reach needs a row:
  `load_settings` raises otherwise (`configured_models` + `require_priced`).

## Conventions

- **No torch, no network in tests** — use `FakeLLM` (scripted responses; subclass to branch
  on `model`). It calls `record_call` like the real clients.
- Model tiers are config, never literals: read `settings.llm.{plan,answer,complex}_model` /
  `escalation_model`; they resolve from the per-provider profile.
- Before writing Claude/Anthropic-specific code (model ids, params), load the `claude-api`
  skill — the current model lineup and param rules live there.
