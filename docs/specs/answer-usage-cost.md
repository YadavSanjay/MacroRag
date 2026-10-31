# answer-usage-cost — report each answer's model cost to the caller

## Requirement

faultline weighs each scenario-enrichment layer's P&L contribution against its latency and cost.
It can time a MacroRAG call but cannot price it: a `POST /query` response carries no token or cost
information, although MacroRAG records every model call's tokens and estimated cost
(`llm.usage.record_call`, priced from `configs/llm_pricing.yaml`) into the request's
`llm.usage.track_usage` aggregate.

When this change is done, every `/query` response (both modes), the `answer` event of
`/query/stream`, and the `macro_query` / `macro_synthesize_shock_vector` MCP results carry a
top-level `usage` block built from the aggregate of that request:

```json
"usage": {"cost_usd": 0.0123, "cost_complete": true, "calls": 4,
          "prompt_tokens": 9000, "completion_tokens": 800, "cached_tokens": 0,
          "total_tokens": 9800, "cached": false}
```

- The block is always present. A request that made no model call (an answer-cache hit in answer
  mode) reports `cost_usd` 0.0 and `calls` 0; faultline reads a missing block as "cost unknown"
  and 0.0 as "free".
- `cost_usd` is the sum of the priced calls' costs, not rounded. When a call's model has no price,
  that call adds nothing to `cost_usd` and `cost_complete` is false: `cost_usd` is then a lower
  bound.
- The block never carries prompts, responses or the per-call log.

The aggregate is opened per request whether or not cost caps are enabled; the cost guard
(`cost.*`, §K) keeps recording the same `cost_usd`.

The graduated synth-grounding C3 tests (`test/spec/synth-grounding/test_sg_c3_mcp_diagnostics.py`)
compare the MCP synthesize body with `synthesis_to_dict(...)` exactly. In this spec PR they are
relaxed to compare the body without its `usage` key, so `main` stays green between this spec
merging and its implementation merging. This spec pins `usage` (C5).

Out of scope: costs outside `record_call` (the RagInference embedder and reranker are a
self-hosted service, not a priced call); `/factors` and the other endpoints; pricing accuracy
(`configs/llm_pricing.yaml` is an estimate).

## Definitions

- **Aggregate**: the `llm.usage.UsageAggregate` of the outermost `track_usage` open during the
  request. Nested `track_usage` blocks (the pipeline's own `query` and `synthesize`) join it.
- **Usage block** of an aggregate `agg` and a flag `cached`:
  `{"cost_usd": agg.cost_usd, "cost_complete": agg.cost_complete, "calls": agg.calls,
  "prompt_tokens": agg.prompt_tokens, "completion_tokens": agg.completion_tokens,
  "cached_tokens": agg.cached_tokens, "total_tokens": agg.prompt_tokens +
  agg.completion_tokens, "cached": cached}`. With `agg` None, every count is 0, `cost_usd` is
  0.0 and `cost_complete` is true. Exactly these eight keys.
- **Cached**: the response's own `cached` flag (the answer cache), copied into the block.

## Acceptance criteria

- **C1**: `api.models.usage_to_dict(agg, *, cached)` returns the usage block of `agg` and
  `cached`, as defined: exactly the eight keys, `cost_usd` a `float`, the counts `int`s. Floats to
  `rel=1e-12`, the rest exact. Oracle: the Definitions applied to aggregates built in the test by
  calling `llm.usage.record_call` inside `track_usage`.
- **C2**: `POST /query` in answer mode, over a pipeline whose LLM double records every call
  through `record_call` with fixed token counts and a priced model id, returns `usage` with
  `calls` equal to the number of calls the double made for that request, the token totals equal
  to calls × the fixed counts, and `cost_usd` equal to the sum of
  `llm.pricing.cost_usd(model, …)` over those calls (`rel=1e-9`), `cost_complete` true and
  `cached` false. The same request repeated (an L1 hit) returns `cached` true, `calls` 0 and
  `cost_usd` 0.0 exactly. This holds with cost caps disabled and enabled. Oracle: the double's
  own call log and the committed price table, computed in the test.
- **C3**: `POST /query` in synthesize mode returns `usage` covering the query step and the
  synthesis pass together: `calls` and `cost_usd` equal the double's calls for that request and
  their summed price. A repeat whose query step is an L1 hit still reports the synthesis pass's
  calls and cost (the shock vector is never cached), with `cached` true. Oracle: as C2.
- **C4**: an unpriced model. With the double recording under a model id that has no entry in the
  price table, `cost_complete` is false and `cost_usd` is the sum of the priced calls only (0.0
  when none is priced); `cost_usd` is always finite and ≥ 0. Oracle: the Definitions.
- **C5**: the other surfaces. The `answer` event of `POST /query/stream` and the results of the
  MCP tools `macro_query` and `macro_synthesize_shock_vector` carry the same `usage` block as the
  matching `POST /query` would (same `calls`, token totals and `cost_usd` for the same request on a
  fresh pipeline; `cached` follows the cache). Oracle: as C2.

## Interface

- `api.models.usage_to_dict(agg: UsageAggregate | None, *, cached: bool) -> dict[str, Any]` — new.
- `POST /query`, `POST /query/stream` (`answer` event), MCP `macro_query`,
  `macro_synthesize_shock_vector` — existing; gain the `usage` key.

## Notes

- The LLM double is a fake at the provider boundary (an `llm.base.LLMClient` subclass in the
  spec's conftest that answers like the cache-scope responder and calls `record_call` with fixed
  token counts). It is not the project's `FakeLLM`, which records zero tokens.
- Priced model ids come from `configs/llm_pricing.yaml` (e.g. `gpt-5.4-mini`); an unpriced id is
  any string with no entry and no prefix match there.
- Fixtures: the cache-scope in-memory pipeline, FastAPI `TestClient`, the MCP server in process.
  No network, no `data/`, no clock.
