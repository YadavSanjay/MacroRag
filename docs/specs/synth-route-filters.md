# synth-route-filters — caller filters and card slots hold on every retrieval route

## Requirement

`QueryPipeline._gather` sends a question down the plan-execute agent route when the planner
returns a quantitative plan. Every faultline synthesis question contains a trigger word ("rise",
"fall", "rates" …), so the planner model decides per call. On that route the pipeline returns
the agent's chunks, and the agent's executor retrieves with **no filter**. So the caller's
`filters` (including the principal stamp the tenant hook relies on) and synthesize's retrieval
options (the reserved scenario-study slots that bring market-episode cards in) are silently
dropped.

Live, 2026-10-15: faultline's `rate_capital` request (publishers Federal Reserve, U.S. Treasury,
CBO and MacroRAG (derived from FRED)) returned 57 chunks, 22 of them BIS, plus BlackRock and
Apollo, and no episode card. All fields were dropped. That was although three sell-off cards pass
every filter in the request.

Decision (user, 2026-10-15):
- **synthesize never takes the agent route.** The shock vector already gets numeric grounding from
  the hydrated series and the cards.
- **On the agent route, a plain answer keeps only chunks that match the caller's filter.**

Out of scope: the agent's planner and executor internals; multi-hop retrieval.

## Acceptance criteria

- **C1**: `QueryPipeline.synthesize` does not take the plan-execute route.
  - **Setup:** a pipeline with a real `PlanExecuteAgent`, whose scripted planner returns a
    quantitative plan, and a corpus of a Federal Reserve note, a BIS note and an episode card
    (publisher `MacroRAG (derived from FRED)`, tagged `cross_asset_scenario`).
  - **Request:** a quantitative question with `base_filter.publishers = ["Federal Reserve",
    "MacroRAG (derived from FRED)"]`.
  - **Result:**
    - the query result's `analysis.route` is not `PLAN_EXECUTE`;
    - the planner prompt is never sent to the model;
    - every returned chunk's publisher is in the filter;
    - the episode card is among the chunks.

  Oracle: the scripted model's call log and the hand-built corpus.
- **C2**: `QueryPipeline.answer` on the plan-execute route.
  - With the same agent and a `base_filter` of `publishers = ["Federal Reserve"]`, the route is
    still `PLAN_EXECUTE`, and every returned chunk's publisher is `Federal Reserve`; the BIS
    chunk the executor retrieved is not returned.
  - With no `base_filter`, the agent's chunks are returned unchanged (both publishers).

  Oracle: the hand-built corpus; `vectorstores.filters.match_payload` semantics (a chunk passes
  when its payload matches the filter).

## Interface

None new. Existing: `QueryPipeline.synthesize`, `QueryPipeline.answer`.

## Notes

- Fixtures: the in-memory pipeline (hashing embedder, in-memory store, identity reranker, NullCache,
  disabled tracer) with a real `PlanExecuteAgent(LLMPlanner(FakeLLM), PlanExecutor(retriever,
  None))`. The scripted FakeLLM answers the planner prompt with a quantitative plan, the answer
  prompt with a summary, and the synthesize prompt with an empty vector. No network, no `data/`,
  no clock.
- After the fix, faultline's `rate_capital` needs a live re-check: it should retrieve the sell-off
  cards under its own publisher filter.
