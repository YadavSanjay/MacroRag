# llm-output-budget — per-tier output budgets, visible refusals and truncation

## Requirement

Every LLM call today gets the same `llm.max_tokens` (4000), the provider's default reasoning
effort and the global `llm.resilience.request_timeout_s` (60 s). Reasoning tokens count against
the output cap on both providers: the Claude 5 models think by default (Sonnet 5.5 at effort
`high`), and the gpt-6 models reportedly default to effort `medium` where gpt-5.4 defaults to
`none`. A reply cut off at the cap, or refused, comes back as partial or empty text: `complete_json`
then pays for a repair call and may silently return `{}`. Nothing records why.

When this change is done:

- **Per-tier options.** Each provider profile may carry `tier_options` (landed inert by the
  chore before this spec): per tier (`plan`, `answer`, `complex`, `default`, `escalation`) an
  optional `max_tokens`, `effort` and `timeout_s`. The options of the **active** profile apply:
  the active provider's `replacement_profiles` entry when the replacement switch is on and that
  entry exists, else its `profiles` entry. The switch is resolved as the model tiers resolve it
  (`use_replacement_models`, overridden by `MACRORAG_USE_REPLACEMENT_MODELS`), so the options
  always belong to the profile that supplied the models. An unset option keeps today's
  behaviour.
- **Effort is validated at load.** Settings loaded from YAML (`load_settings_from`) raise
  `ValueError` when any profile or replacement profile sets an effort outside its provider's set
  (Notes). Whether a given model accepts a given effort is checked by the live eval, not here.
- **Calls carry their tier.** A provider client reads the tier from `llm.usage.current_tier()`
  (set with `tier_context`) and applies that tier's options:
  - OpenAI: `max_completion_tokens` = the tier's `max_tokens`, else `llm.max_tokens`;
    `reasoning_effort` = the tier's `effort`, sent only when set; `timeout` = the tier's
    `timeout_s`, sent per request only when set.
  - Anthropic: `max_tokens` likewise; `output_config={"effort": <effort>}` only when set;
    `timeout` per request only when set.
  - Outside any tier, or for a tier with no options, the request is as today.
- **Call sites set the tier** of the setting that supplied the model: `plan` for planning
  (`plan`, `plan_execute`), `multi_hop` and `verify`; `answer` for `generate` on a route not in
  `llm.complex_routes` and `complex` on one in it; `complex` for `synthesize` and
  `discover_factors`; for an escalated re-run (`generate:escalated`, `synthesize:escalated`),
  `complex` when the escalated model is `llm.complex_model`, else `escalation`. The eval judge
  sets none.
- **Finish reasons are normalized and recorded.** `normalize_finish_reason(provider, raw)`
  maps Anthropic `end_turn`/`stop_sequence` → `stop`, `max_tokens` → `length`, `refusal` →
  `refusal`; OpenAI `stop` → `stop`, `length` → `length`, `content_filter` → `refusal`; any
  other value passes through unchanged; `None` stays `None`. Each client passes the normalized
  reason, the reasoning tokens (OpenAI `usage.completion_tokens_details.reasoning_tokens`, else
  0; Anthropic reports none, so 0) and the response's model id (`served_model`) to
  `record_call`, sets `last_finish_reason` on itself, and `CallRecord.as_dict()` includes the
  three fields. `record_call` logs a WARNING when `served_model` is given and is neither the
  requested model nor a dated snapshot of it: the requested model, `-`, then a suffix that
  starts with a digit (`gpt-5.4-2026-03-05`). `gpt-5.4-mini` served for `gpt-5.4` warns.
  A reason mapped for one provider is not mapped for the other: it passes through unchanged.
- **Refusals fail soft.** On a `refusal` the client records the call (its tokens are billed)
  and then raises `llm.base.LLMRefusalError` carrying the requested model and the refusal
  category (Anthropic `stop_details.category`, else `None`). `complete_json` catches it: no
  repair call follows a refused first reply; it records the outcome `refused` and returns
  `{}`. A refused repair call records `refused` and returns the first reply's best-effort
  parse. Without a schema, a refused reply returns `{}` and records nothing.
- **Truncation is visible.** When any reply of a `complete_json` call ends with `length`, the
  call records `truncated` once (even when both the reply and its repair are cut off). The
  repair call still happens when the reply fails its schema.
- **Schema-health counts.** Each `complete_json` with a schema records exactly one of `ok`,
  `repaired`, `failed`, `refused`, plus `truncated` when a reply was cut off.
  `SchemaCounts.as_dict()` adds `refused` and `truncated`; `total` counts `ok + repaired +
  failed + refused` (not `truncated`, which marks calls already counted); `repair_rate` stays
  `(repaired + failed) / total`. The `/admin/schema-health` totals include both new counts.
- **The answer-cache key follows what shapes an answer.** `cache_version(settings)` hashes the
  answer prompt, `plan_model`, `answer_model`, `complex_model`, `llm.max_tokens` and the active
  profile's options for `plan`, `answer` and `complex`; `QueryPipeline` uses it.
- **`retry-after` is honoured.** When a retryable provider error carries `retry-after-ms`
  (milliseconds) or `retry-after` (seconds) in `exc.response.headers`, `call_with_retry` waits
  that long, capped at `backoff_max_s`, instead of its jittered backoff. A value is usable when
  it is a finite number ≥ 0 (decimals allowed); a missing, negative, non-finite or non-numeric
  value (e.g. an HTTP date) is not. The first usable of `retry-after-ms`, then `retry-after`,
  sets the wait; with neither usable, the jittered backoff applies.

## Acceptance criteria

- **C1**: `utils.config.tier_options_for(llm, tier)` returns the active profile's `TierOptions`
  for `tier` (Requirement, "Per-tier options"), and `TierOptions()` (all `None`) when `tier` is
  `None`, the tier has no entry, or the active provider has no profile. It follows
  `use_replacement_models` and `llm.provider`. Oracle: Requirement.
- **C2**: `utils.config.load_settings_from(path)` raises `ValueError` for a YAML file that sets
  an effort outside the provider's set (Notes) in any profile or replacement profile of either
  provider, and loads a file whose efforts are all in the sets. Oracle: Notes effort sets.
- **C3**: `llm.openai_client.OpenAIClient(settings).complete(...)`, with the `openai` SDK
  replaced by a fake that records `chat.completions.create(**kwargs)`, sends the per-tier
  `max_completion_tokens`, `reasoning_effort` and `timeout` of the Requirement for the tier set
  with `tier_context`, omits `reasoning_effort` and `timeout` when unset, and sends today's
  request outside any tier. Oracle: Requirement.
- **C4**: `llm.anthropic_client.AnthropicClient(settings).complete(...)`, with a fake
  `anthropic` SDK recording `messages.create(**kwargs)`, sends the per-tier `max_tokens`,
  `output_config={"effort": e}` and `timeout` likewise, and today's request outside any tier.
  Oracle: Requirement.
- **C5**: `llm.base.normalize_finish_reason(provider, raw)` follows the Requirement table, and
  through both clients (fake SDKs): the `CallRecord` in the `track_usage` aggregate carries the
  normalized `finish_reason`, `reasoning_tokens` (OpenAI's reported count; 0 for Anthropic) and
  `served_model`; `as_dict()` includes them; the client's `last_finish_reason` matches; a
  `length` reply returns its partial text; a `refusal` raises `LLMRefusalError` with the model and
  category after the call is recorded with its cost. `record_call` warns when `served_model`
  differs from the requested model other than by a `-`-suffix. Oracle: Requirement.
- **C6**: `LLMClient.complete_json` with a schema, over an `LLMClient` test double whose
  `complete` returns scripted replies and sets `last_finish_reason`, or raises `LLMRefusalError`:
  a refused first reply makes exactly one `complete` call, returns `{}` and records `refused`;
  a refused repair returns the first reply's parse and records `refused`; a valid `length`
  reply records `ok` and `truncated`; an invalid `length` reply is repaired (two calls) and
  records `truncated` plus `repaired` or `failed`. `SchemaCounts.as_dict()` and
  `schema_health_snapshot()["totals"]` carry `refused` and `truncated`, with `total` and
  `repair_rate` as in the Requirement. Oracle: Requirement and the user's decisions (refusals
  fail soft without repair; truncation keeps the repair).
- **C7**: Through `QueryPipeline.answer` and `QueryPipeline.synthesize` on the in-memory test
  pipeline (hashing embedder, in-memory store, identity reranker, an `LLMClient` test double
  that records `llm.usage.current_tier()` per `purpose`), every call runs under the tier the
  Requirement assigns to its purpose, for a simple route, a complex route, synthesis, and a
  prose escalation (answer → complex). Oracle: Requirement ("Call sites set the tier").
- **C8**: `pipelines.query_pipeline.cache_version(settings)` is equal for equal settings,
  changes when any of its inputs (Requirement) changes, including the active profile's
  `plan`/`answer`/`complex` options, and is unchanged when an unrelated setting (e.g.
  `trace.enabled`) or another provider's profile options change. `QueryPipeline` built from
  settings uses it (a changed tier option misses an answer cached under the old one). Oracle:
  Requirement and `.claude/rules/rag.md` (cache keys change with model tier).
- **C9**: `llm.resilience.call_with_retry` with an injected `sleep` and `rand`, retrying a
  429 error whose `response.headers` hold `retry-after: 7`, sleeps 7; with `retry-after: 120`
  and `backoff_max_s` 30, sleeps 30; with `retry-after-ms: 2500` (and `retry-after: 9`), sleeps
  2.5; with no header, an HTTP-date value or a negative value, sleeps today's jittered delay
  (`min(backoff_max_s, backoff_base_s * 2**attempt) * rand()`). Oracle: Requirement.

## Interface

New:

- `llm.base.LLMRefusalError(model: str, category: str | None = None)` — a `RuntimeError` with
  attributes `model` and `category`.
- `llm.base.normalize_finish_reason(provider: str, raw: str | None) -> str | None`
- `utils.config.tier_options_for(llm: LLMSettings, tier: str | None) -> TierOptions`
- `pipelines.query_pipeline.cache_version(settings: RagSettings) -> str`

Existing and exercised as is: `utils.config.TierOptions`, `ModelProfile.tier_options`,
`load_settings_from`, `LLMSettings`, `RagSettings`; `llm.usage.tier_context`, `current_tier`,
`record_call`, `track_usage`, `current_usage`, `CallRecord`; `llm.schema_health` (`REFUSED`,
`TRUNCATED`, `SchemaCounts`, `capture_schema`, `schema_health_snapshot`); `llm.base.LLMClient`;
the two provider clients; `llm.resilience.call_with_retry`; `pipelines.query_pipeline.QueryPipeline`.

## Notes

- **Effort sets.**
  - Anthropic (`output_config.effort`): `low`, `medium`, `high`, `xhigh`, `max` (claude-api
    reference, read 2026-10-21). Not every model accepts every level (Sonnet 4.6 has no
    `xhigh`; Haiku 4.5 takes no effort), which the live eval catches.
  - OpenAI (`reasoning_effort`): `none`, `low`, `medium`, `high`, `xhigh`, `max`. gpt-5.4 lists
    `none` (default), `low`, `medium`, `high`, `xhigh` on its model page
    (developers.openai.com, read 2026-10-21); gpt-6 Sol / Luna reportedly add `max` and default
    to `medium` (third-party coverage of OpenAI's GPT-6 guide; the official page was not
    reachable). The set is their union.
- **Fake SDKs (C3–C5)**: module objects placed at `sys.modules["openai"]` / `["anthropic"]`
  with pytest's `monkeypatch`, recording each create call's kwargs and returning namespaces
  shaped like the SDK responses (OpenAI: `choices[0].message.content`,
  `choices[0].finish_reason`, `usage.prompt_tokens`, `completion_tokens`, `total_tokens`,
  `prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens`, `model`;
  Anthropic: `content` text blocks, `stop_reason`, `stop_details.category`, `usage`, `model`).
  API keys are set with `monkeypatch.setenv`. These fake the provider at the I/O boundary only.
- **In-memory pipeline (C7, C8)**: as in the cache-scope / answer-usage-cost spec conftests.
- Tests set `MACRORAG_DOTENV=0` before importing `utils.config` and unset
  `MACRORAG_USE_REPLACEMENT_MODELS`; YAML they load is written under `tmp_path`.
- **Out of scope:**
  - The tier values themselves: `configs/rag.yaml` sets no `tier_options` in this change. The
    numbers come from the live eval (cut-off rate, repair rate, reasoning tokens, cost).
  - Whether gpt-5.4 accepts `temperature` with a reasoning effort above `none`: no tier sets
    one until the live eval has checked it.
  - Anthropic `thinking` configuration (`between_tools` for a Sonnet 5.5 plan tier).
  - Streaming: non-streaming is safe below about 21K `max_tokens` (SDK 0.109.1 rule).
