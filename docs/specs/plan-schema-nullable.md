# plan-schema-nullable — the plan reply may leave optional fields null

## Requirement

The query planner's reply is validated against `prompts/tasks/plan/schema.json`, which
types `paraphrase` as a string and `subqueries` / `hyde` as arrays. Models commonly answer
`"paraphrase": null` when there is nothing to paraphrase. In the 2026-10-21 live pilot,
17 of 20 `plan` replies (gpt-5.4-mini, gpt-6-sol, claude-sonnet-4-6, claude-sonnet-5-5)
failed the schema for exactly that reason, so `complete_json` made a repair call on most
queries, adding cost and latency. On claude-sonnet-4-6 the repair failed again five times.
The reply was usable all along: `LLMAnalyzer.analyze` already treats a null `paraphrase`,
`subqueries` or `hyde` as absent.

When this change is done:

- The plan schema accepts `null` for `paraphrase`, `subqueries` and `hyde`, in addition to
  their current types. `route`, when present, must still be a string, and every other type
  rule stays (`paraphrase` a string, `subqueries` and `hyde` arrays, when not null).
  Additional properties stay allowed.
- A plan reply with null optional fields therefore validates first time: no `plan:repair`
  call, and the schema-health outcome is `ok`. The analysis built from it is unchanged
  (route from `route`; no paraphrase; subqueries and HyDE documents only from non-empty
  arrays).

## Acceptance criteria

- **C1**: With the plan task's schema as the registry resolves it
  (`llm.registry.get_registry().resolve("plan").schema`), `llm.base.schema_errors(obj,
  schema)` is empty for every reply whose `route` is a string and whose `paraphrase`,
  `subqueries` and `hyde` are each absent, null or of their type (string / array / array),
  and non-empty when `route` is null or not a string, `paraphrase` is a non-null
  non-string (e.g. `3`, `["x"]`), or `subqueries` / `hyde` is a non-null non-array (e.g.
  `"x"`, `3`). Oracle: Requirement.
- **C2**: `llm.understanding.LLMAnalyzer(llm, RetrievalSettings(), plan_model=<id>)
  .analyze(<question>)`, with an `LLMClient` test double that replies `{"route":
  "DECOMPOSE", "paraphrase": null, "subqueries": ["a", "b"], "hyde": null}`, calls
  `complete` exactly once (purpose `plan`, no `plan:repair`), records exactly one `ok`
  outcome under `llm.schema_health.capture_schema()` (no `repaired` or `failed`), and returns
  an analysis with route `DECOMPOSE`, no paraphrase, subqueries `["a", "b"]` and no HyDE
  documents. For any combination of null / valid values of the three fields, the call count
  stays one and the outcome `ok`. Oracle: Requirement and the pilot's recorded replies.

## Interface

No new functions. Exercised as is: `llm.registry.get_registry`, `llm.base.schema_errors`,
`llm.base.LLMClient`, `llm.understanding.LLMAnalyzer`, `utils.config.RetrievalSettings`,
`llm.schema_health.capture_schema`.

## Notes

- The pilot replies were read from the query traces of 2026-10-21 (`call_log[].response`
  for purpose `plan`); every schema error was `paraphrase: None is not of type 'string'`.
- The registry's `content_hash` covers the system prompt and user template, not
  `schema.json`, so this change leaves prompt provenance unchanged.
- Tests set `MACRORAG_DOTENV=0` before importing `utils.config`. The test double stands in
  for the provider at the `LLMClient` boundary.
