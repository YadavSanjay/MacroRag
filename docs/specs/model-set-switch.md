# model-set-switch — a config switch to replacement OpenAI and Claude models, off by default

## Requirement

The current model tiers run on models that are being superseded:

- **OpenAI** (active provider): `gpt-5.4-nano` (answer and default) shuts down on 2027-04-01,
  and OpenAI names `gpt-6-luna` as its replacement. `gpt-5.4` and `gpt-5.4-mini` have no
  deprecation.
- **Anthropic** (alternate provider): the profile names `claude-sonnet-4-6`, `claude-haiku-4-5`
  and `claude-opus-4-8`, which predate the Claude 5 family.

A second **model set** is declared beside today's, per provider: OpenAI plan `gpt-6-sol`,
answer and default `gpt-6-luna`, complex `gpt-6-sol`; Anthropic plan `claude-sonnet-5-5`,
answer and default `claude-haiku-5-5`, complex `claude-opus-5-5`. It is selected by a **feature
switch, `llm.use_replacement_models`, off by default**. This change moves nothing: with the
switch off every tier of both providers keeps exactly today's model. With it on, the active
provider's tiers come from the replacement set.

The environment variable `MACRORAG_USE_REPLACEMENT_MODELS` overrides the config value for a
process, so an eval run can try the replacement set without a config edit. A value it doesn't
understand raises instead of choosing a set. A flat `plan_model` / `answer_model` /
`complex_model` set explicitly still wins over either set, as today.

Turning the switch on must not fail on request parameters:

- The Anthropic client sends `temperature` to every model except those named in
  `_NO_SAMPLING_PREFIXES`. `claude-opus-5-5` and `claude-haiku-5-5` are not covered by it, and
  both reject sampling parameters with HTTP 400. Every Claude 5-family id (Opus, Sonnet, Haiku,
  Fable, Mythos) must be called without `temperature`; the 4.6-and-older ids keep it.
- The OpenAI client sends `temperature` on every call. `gpt-6-sol` and `gpt-6-luna` reject any
  value but the default with HTTP 400 (smoke call, 2026-10-17), so the client must omit it for
  every `gpt-6` id; the `gpt-5.4` family keeps it.

Every model in either set is priced in `configs/llm_pricing.yaml`, so cost accounting stays
complete whichever set is active.

Out of scope: the escalation tier, effort, reasoning or thinking parameters, cache-write
pricing, and choosing whether to switch (eval runs decide that later).

## Acceptance criteria

- **C1**: `default_profiles(False)` returns, per provider, the tiers `plan`, `answer`, `complex`,
  `default`: `openai` → `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.4`, `gpt-5.4-nano`;
  `anthropic` → `claude-sonnet-4-6`, `claude-haiku-4-5`, `claude-opus-4-8`, `claude-haiku-4-5`.
  Exact match. Oracle: `_DEFAULT_PROFILES` on `main` (copied into the test).
- **C2**: `default_profiles(True)` returns `openai` → `gpt-6-sol`, `gpt-6-luna`, `gpt-6-sol`,
  `gpt-6-luna`; `anthropic` → `claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-opus-5-5`,
  `claude-haiku-5-5`. Each call returns a fresh copy: mutating a result changes no later
  result. Oracle: OpenAI deprecations page (`gpt-5.4-nano` → `gpt-6-luna`); Anthropic models
  overview (current Sonnet, Haiku and Opus ids, 2026-10); the OpenAI plan and complex tiers are
  the operator's choice recorded in Notes.
- **C3**: `replacement_models_enabled(env, default)` returns `default` when `env` has no
  `MACRORAG_USE_REPLACEMENT_MODELS` key. When the key is present, `"1"`, `"true"`, `"yes"`,
  `"on"` mean `True` and `"0"`, `"false"`, `"no"`, `"off"` mean `False`, ignoring case and
  surrounding whitespace, whatever `default` is; any other value (including empty) raises
  `ValueError`. `env=None` reads `os.environ`. Oracle: requirement (fail loud on unknown values).
- **C4**: `LLMSettings` resolves its tiers from the selected set, for either provider. With the
  variable unset, `LLMSettings(provider=p)` has `plan_model`, `answer_model`, `complex_model`,
  `model` equal to C1's `p` tiers (plan, answer, complex, default), and
  `LLMSettings(provider=p, use_replacement_models=True)` has C2's `p` tiers. The variable
  overrides the field in both directions. An explicit flat tier (e.g. `plan_model="gpt-5.4-mini"`)
  wins over the replacement set. Oracle: requirement and C1/C2.
- **C5**: The committed `configs/rag.yaml`, loaded by `load_settings()` with the variable unset,
  gives `llm.provider == "openai"`, the C1 OpenAI tiers (switch off), and an Anthropic profile
  (`llm.profiles["anthropic"]`) equal to C1's Anthropic tiers. With the variable set to `"1"` it
  gives C2's OpenAI tiers. Oracle: requirement (this change moves no model).
- **C6**: `llm.anthropic_client._accepts_temperature(model)` is `False` for every id starting
  with `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-5`, `claude-fable-5`, `claude-mythos-5`,
  `claude-opus-4-8` or `claude-opus-4-7`, whatever follows, and `True` for `claude-haiku-4-5`,
  `claude-sonnet-4-6`, `claude-opus-4-6`. Oracle: Anthropic API reference, sampling parameters
  per model (removed on Opus 4.7 and later and the Fable/Mythos 5 family; non-default values
  rejected on Sonnet 5.5 and Haiku 5.5; accepted on 4.6 and older).
- **C7**: `llm.pricing.cost_usd` prices every model in either set. Rates (USD per 1M tokens,
  input / cache read / output): `claude-opus-5-5` 4.00 / 0.20 / 20.00, `claude-sonnet-5-5`
  2.00 / 0.20 / 10.00, `claude-haiku-5-5` 0.10 / 0.01 / 0.50, `gpt-6-sol` 2.00 / 0.20 / 10.00,
  `gpt-6-luna` 0.10 / 0.01 / 0.50; the current models keep `gpt-5.4` 2.50 / 0.25 / 15.00,
  `gpt-5.4-mini` 0.75 / 0.075 / 4.50, `gpt-5.4-nano` 0.20 / 0.02 / 1.25, `claude-opus-4-8`
  5.00 / 0.50 / 25.00, `claude-sonnet-4-6` 3.00 / 0.30 / 15.00, `claude-haiku-4-5`
  1.00 / 0.10 / 5.00. Within `abs=1e-9` USD. Oracle: Anthropic pricing (Claude API, prompts
  ≤ 100K tokens for Haiku 5.5) and OpenAI pricing (Standard, short context), both read
  2026-10-17.
- **C8**: `llm.openai_client.accepts_temperature(model)` is `False` for every id starting with
  `gpt-6`, whatever follows, and `True` for `gpt-5.4`, `gpt-5.4-mini` and `gpt-5.4-nano`.
  Oracle: OpenAI API error for `temperature=0.1` on `gpt-6-sol` and `gpt-6-luna` ("Only the
  default (1) value is supported", 2026-10-17); the `gpt-5.4` family runs with 0.1 in
  production today.

## Interface

- `utils.config.default_profiles(use_replacement: bool) -> dict[str, dict[str, str]]`
- `utils.config.replacement_models_enabled(env: Mapping[str, str] | None = None, default: bool = False) -> bool`
- `llm.openai_client.accepts_temperature(model: str) -> bool`

`LLMSettings` gains `use_replacement_models: bool = False` (and may gain a
`replacement_profiles` map) in the implementation; spec tests pass it as a keyword only.

## Notes

- Sources: https://developers.openai.com/api/docs/deprecations (`gpt-5.4-nano` shutdown
  2027-04-01, announced 2026-10-01, replacement `gpt-6-luna`; no entry for `gpt-5.4` or
  `gpt-5.4-mini`), https://developers.openai.com/api/docs/pricing (read 2026-10-17), and the
  Anthropic models overview and pricing pages (Haiku 5.5 bills $0.50 / $2.50 above 100K-token
  prompts; MacroRAG prompts stay below that).
- Replacement OpenAI plan and complex tiers: the operator chose `gpt-6-sol` for both on
  2026-10-17. It is newer than `gpt-5.4` and cheaper per token than both `gpt-5.4` and
  `gpt-5.6-terra`. Planning on `gpt-6-luna` is not chosen: nano-class planning broke retrieval
  before (`configs/rag.yaml` comment on the plan tier).
- The operator asked (2026-10-17) that this change only set up the replacement models behind
  the switch, off: no tier of either provider changes model.
- Smoke calls on 2026-10-17 through the project clients, with the parameter fixes above:
  `gpt-6-sol`, `gpt-6-luna`, `claude-haiku-5-5`, `claude-sonnet-5-5` and `claude-opus-5-5` all
  answered.
- Fixtures: none beyond the committed configs. Tests unset `MACRORAG_USE_REPLACEMENT_MODELS`
  unless they set it, and set `MACRORAG_DOTENV=0`. No network, no `data/`, no clock.
- Guide 04 (LLM provider/tier config) and the LLM harness design doc are updated in the
  implementation PR.
