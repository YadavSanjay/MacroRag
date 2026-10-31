# llm-model-registry — every configured model is priced, and cache writes are costed

## Requirement

The rate card `configs/llm_pricing.yaml` prices every model call, and the per-request total feeds
the cost guard's daily caps (402, §K), the escalation budget and the answer `usage` block. Three
gaps make that total wrong without any error:

1. **An unpriced model costs $0.** A model with no row logs a warning, its calls carry
   `cost_usd: null`, and it adds nothing to the request total. Nothing stops `configs/rag.yaml`
   (a profile, a replacement profile, an escalation tier, a flat `*_model` override) or
   `eval-matrix --models` from naming such a model.
2. **Anthropic cache writes are charged at the plain input rate.** The Anthropic client sends a
   `cache_control` breakpoint on the system prompt, so `cache_creation_input_tokens` is often
   non-zero. Anthropic bills those tokens at 1.25 × the input rate (5-minute cache).
3. **A mistyped rate-card key is silent.** A row with `cache_wirte` loads, and the rate it meant
   to set falls back to `input`.

When this change is done:

- **Load-time completeness.** Settings loaded from a YAML file raise `ValueError` naming every
  configured model that has no rate-card row. "Configured" means every model id in every
  provider's `profiles` and `replacement_profiles` entry (`plan`, `answer`, `complex`, and
  `default` / `escalation` when set), plus the resolved `model`, `plan_model`, `answer_model`,
  `complex_model` and (when set) `escalation_model`. All providers are checked, not only the
  active one, so switching `llm.provider` or `llm.use_replacement_models` can never land on an
  unpriced model. `load_settings()` loads `configs/rag.yaml` through the same path, so the
  committed config must pass.
- **eval-matrix.** The `eval-matrix` command resolves its model list and judge model through one
  function, which raises `ValueError` naming every unpriced id before any golden set is read or
  any pipeline is built.
- **Cache writes.** A rate-card row may carry `cache_write` (USD per 1M tokens). A call's cost is
  `(uncached × input + cached × cache_read + written × cache_write + completion × output) / 1e6`,
  where `uncached = prompt − cached − written`, `cache_read` and `cache_write` default to `input`
  when absent, and `prompt` includes the cached and written tokens. The Anthropic client prices
  each call this way from `cache_read_input_tokens` and `cache_creation_input_tokens`. Every
  Anthropic row in the committed card carries `cache_write = 1.25 × input`.
- **Long prompts.** A row may carry `max_priced_prompt_tokens`. A call whose prompt is longer logs
  a WARNING naming the model, because its real rate is higher than the card's. The committed
  `claude-haiku-5-5` row sets 100000 (Anthropic charges $0.50 / $2.50 above 100K prompt tokens).
  The cost itself is unchanged: only the warning is added.
- **Validated rate card.** The card is validated when it is first loaded: a top-level `models`
  mapping, each row with numeric `input` and `output`, optional `cache_read`, `cache_write` and
  `max_priced_prompt_tokens`, and no other key. A violation raises `ValueError`.

Unchanged: a call to an unpriced model at run time still returns cost `None` and is reported as
`cost_complete: false` (spec answer-usage-cost); `llm.pricing.cost_usd` keeps its signature and
results (spec model-set-switch C7), and equals the new cost with no cache writes.

## Acceptance criteria

- **C1**: `llm.pricing.require_priced(models)` returns `None` when every id has a rate-card row
  (exact match, or the longest row name the id starts with, as `llm.pricing.model_price`
  resolves it), and otherwise raises `ValueError` whose message contains every unpriced id.
  An empty list passes. Repeating an id, or reordering the ids, changes neither outcome. Oracle: Requirement
  (eval-matrix, load-time completeness) and the committed card's row names.
- **C2**: `utils.config.configured_models(llm)` returns the sorted, de-duplicated list of the
  model ids defined under "Configured" in the Requirement. For an `LLMSettings` built in the test
  from hand-written profiles, the result equals the list the test writes out by hand; adding an
  `escalation` model to any profile of either set adds that id. Oracle: Requirement.
- **C3**: `utils.config.load_settings_from(path)` loads a YAML file exactly as `load_settings()`
  loads `configs/rag.yaml` (same `model_dump()` for the committed file), and raises `ValueError`
  naming the id when the file names an unpriced model in any of these places: a flat
  `answer_model`, the `escalation` of a profile of a provider that is not active, a
  `replacement_profiles` tier. For a file whose models are all priced it returns settings.
  Oracle: Requirement and the committed `configs/rag.yaml`.
- **C4**: `pipelines.cli.matrix_models(models_arg, judge_model_arg, llm)` returns
  `(models, judge_model)`: `models` is the comma-split, whitespace-stripped, non-empty entries of
  `models_arg` in their given order, or `[llm.answer_model, llm.complex_model]` when
  `models_arg` is `None` or empty; `judge_model` is `judge_model_arg`, or `llm.plan_model` when it
  is `None` or empty. It raises `ValueError` naming every unpriced id among `models` and
  `judge_model`. Oracle: Requirement and the current `eval-matrix` argument handling in
  `pipelines/cli.py`.
- **C5**: `llm.pricing.call_cost(model, prompt_tokens=P, completion_tokens=O, cached_tokens=R,
  cache_write_tokens=W)` equals the Requirement's formula over the committed card within
  `abs=1e-12` USD, for the Anthropic rows at the rates in Notes. It returns `None` for an unpriced
  model, raises `ValueError` when a count is negative or `R + W > P`, and equals
  `llm.pricing.cost_usd(model, prompt_tokens=P, completion_tokens=O, cached_tokens=R)` whenever
  `W == 0`. Oracle: Anthropic prompt-caching pricing (Notes) and the Requirement formula.
- **C6**: `llm.anthropic_client.AnthropicClient.complete`, with the `anthropic` SDK replaced by a
  fake whose response usage has `input_tokens` 1000, `cache_read_input_tokens` 4000,
  `cache_creation_input_tokens` 2000 and `output_tokens` 500 for model `claude-sonnet-4-6`, adds
  `(1000 × 3.00 + 4000 × 0.30 + 2000 × 3.75 + 500 × 15.00) / 1e6 = 0.0192` USD to the
  `track_usage` aggregate (`abs=1e-12`). For any non-negative usage counts the aggregate cost
  equals `call_cost` over the same counts (prompt = input + read + creation). Oracle: Notes rates
  and a hand calculation.
- **C7**: `call_cost` for `claude-haiku-5-5` logs a WARNING on logger `llm.pricing` whose message
  contains `claude-haiku-5-5` when `prompt_tokens` is 100001, and logs no such WARNING at 100000.
  Every such call warns (not only the first per model), and the cost is the same as below the
  limit. `claude-haiku-5-5` is the only committed row with `max_priced_prompt_tokens`; for any
  other row, no prompt length logs the warning. Oracle: Anthropic
  Haiku 5.5 pricing (Notes).
- **C8**: `llm.pricing.validate_rate_card(data)` returns `None` for the committed card (as parsed
  from `configs/llm_pricing.yaml`) and for any card built from valid rows, and raises
  `ValueError` for: no `models` mapping; a top-level key other than `models`; a row missing
  `input` or `output`; a row with a key outside `input`, `output`, `cache_read`, `cache_write`,
  `max_priced_prompt_tokens`; a rate that is negative, non-finite, a bool or not a number; a
  `max_priced_prompt_tokens` that is not an int greater than 0 (a bool or a float such as
  `100000.0` is not an int here).
  Oracle: Requirement.

## Interface

New functions:

- `llm.pricing.require_priced(models: Iterable[str]) -> None`
- `llm.pricing.call_cost(model: str, *, prompt_tokens: int = 0, completion_tokens: int = 0,
  cached_tokens: int = 0, cache_write_tokens: int = 0) -> float | None`
- `llm.pricing.validate_rate_card(data: Mapping[str, object]) -> None`
- `utils.config.configured_models(llm: LLMSettings) -> list[str]`
- `utils.config.load_settings_from(path: Path) -> RagSettings`
- `pipelines.cli.matrix_models(models_arg: str | None, judge_model_arg: str | None,
  llm: utils.config.LLMSettings) -> tuple[list[str], str]`

Existing and exercised as is: `llm.pricing.cost_usd`, `llm.pricing.model_price`,
`utils.config.load_settings`, `utils.config.LLMSettings` / `ModelProfile` / `RagSettings`,
`llm.anthropic_client.AnthropicClient(settings)` with `complete(...)`,
`llm.usage.track_usage` / `current_usage`.

Wiring the tests cannot reach, which review checks: `load_settings()` delegates to
`load_settings_from`; `_cmd_eval_matrix` calls `matrix_models` before `load_golden` and
`build_pipeline`; the rate-card loader calls `validate_rate_card`; `cost_usd` and the Anthropic
client price through `call_cost`.

## Notes

- **Rates** (USD per 1M tokens; Anthropic pricing via the claude-api reference, read 2026-10-21;
  cache writes are the 5-minute TTL, 1.25 × input):

  | Model | input | cache_read | cache_write | output |
  |---|---|---|---|---|
  | claude-opus-5-5 | 4.00 | 0.20 | 5.00 | 20.00 |
  | claude-sonnet-5-5 | 2.00 | 0.20 | 2.50 | 10.00 |
  | claude-haiku-5-5 | 0.10 | 0.01 | 0.125 | 0.50 |
  | claude-opus-4-8 | 5.00 | 0.50 | 6.25 | 25.00 |
  | claude-opus-4-7 | 5.00 | 0.50 | 6.25 | 25.00 |
  | claude-sonnet-4-6 | 3.00 | 0.30 | 3.75 | 15.00 |
  | claude-haiku-4-5 | 1.00 | 0.10 | 1.25 | 5.00 |

  The OpenAI rows get no `cache_write`: OpenAI does not bill cache writes separately.
- **Haiku 5.5 long prompts:** $0.10 / $0.50 per 1M up to 100K prompt tokens, $0.50 / $2.50
  above. The card keeps the short-prompt rate and only warns: MacroRAG prompts are top-k chunks,
  far below 100K.
- **Fake SDK (C6).** A module object placed at `sys.modules["anthropic"]` with pytest's
  `monkeypatch`. Its `Anthropic(**kwargs)` accepts any keywords and returns a client whose
  `messages.create(**kwargs)` returns a namespace with `content` (one block with `type` "text"
  and `text`) and `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`). `ANTHROPIC_API_KEY` is set with `monkeypatch.setenv`. This
  fakes the provider at the I/O boundary only.
- Tests set `MACRORAG_DOTENV=0` before importing `utils.config`, unset
  `MACRORAG_USE_REPLACEMENT_MODELS`, and write any YAML they load under `tmp_path`.
- **Out of scope:**
  - Per-model capability columns (`accepts_temperature`, `max_output`). The model-set-switch
    tests (C6, C8) lock family-prefix temperature rules for any id suffix, including ids with no
    rate-card row, which a per-row table cannot express; `max_output` moves with per-tier
    output budgets to spec llm-output-budget.
  - Longest-prefix row matching, which can price an unlisted variant (e.g. a future
    `gpt-5.4-pro`) at its base model's rate.
  - Validating `LLMSettings` built in code: only the file-loading path checks completeness, so
    tests that build settings with fake model ids are unaffected.
