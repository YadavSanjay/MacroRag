# synth-grounding — synthesize sees the quantified scenario evidence and reports honestly

## Requirement

A shock-vector synthesis must be able to see the corpus's quantified scenario studies, and
its response must say truthfully what happened and which corpus it ran against.

Evidence (faultline ScenarioGen run for "Taiwan Strait Blockade: TSMC
Advanced-Node Export Halt"): synthesize returned `llm_empty` over 23 dated chunks. Four
MacroRAG defects contributed:

1. **Substring tag matching.** `retriever.analysis.extract_filters` matched vocabulary cues
   as raw substrings, so the entity cue `ai` fired on "m**ai**time/**ai**r", "Str**ai**t" and
   "ch**ai**ns". The query was tagged `entity_tags=[semiconductors, ai]`.
2. **Hard entity facet shuts out the scenario studies.** Facets are AND-combined, so the
   heuristic entity facet excluded the two quantified analogs the synthesize prompt tells the
   model to use: EBA/ESRB 2025 adverse (`entity_tags: []`) and IMF GFSR Apr-2025 Ch.2
   (`entity_tags: [china]`). Both passed the factor facet. What was left was qualitative
   Taiwan prose, and the model correctly found nothing to anchor a magnitude to.
3. **MCP drops the diagnostics.** `macro_synthesize_shock_vector` omits
   `synthesis_diagnostics`, which REST `/query` (mode `synthesize`) returns. The MCP module
   documents that both surfaces return identical JSON. An MCP consumer therefore cannot see
   `outcome: llm_empty` / `coverage_gap: true` and cannot fail loud on an empty vector.
4. **Fabricated provenance in prose.** The caller's question asked to "return
   world_view_id". The prose summary then asserted `world_view_id: "taiwan_strait_blockade_…"`,
   which contradicts the real, manifest-derived id carried in the same response.

Out of scope: retrieval latency (separate change: it needs live measurement and touches every
route), the faultline-side timeout and grounding gate, and `macro_query` / `discover_factors`
responses (C4 covers synthesize responses only).

## Acceptance criteria

- **C1**: `extract_filters(query)` matches a vocabulary cue (a literal tag name with `_` read
  as a space, or a synonym phrase) only as whole words: the match starts at a word boundary
  and ends at a word boundary, optionally after one trailing `s` (a plural). Case-insensitive.
  A cue that appears only inside a longer word yields no tag. So "PRC maritime/air blockade
  around the Taiwan Strait disrupts supply chains" yields no `ai` entity tag but does yield
  the `china_taiwan_tail` factor tag; "AI capex and hyperscaler guidance" yields entity tags
  `ai` and `hyperscaler`; "export bans on chips" yields entity `semiconductors`. Exact set
  membership. Oracle: the regex definition of a word boundary (`\b`) applied by hand to the
  example strings.
- **C2**: In `QueryPipeline.synthesize`, entity tags that came **only** from heuristic
  extraction (the caller's `base_filter` is `None` or has empty `entity_tags`; other caller
  facets may be set) do not exclude a chunk whose `factor_tags`
  include `cross_asset_scenario` or `stress_scenario`, provided the chunk passes every other
  facet (factor tags, dates, tiers, doc types, publishers, buckets). Such a chunk, if it ranks,
  appears in the synthesis result's `query.chunks`. Everything else is unchanged:
  - a chunk without either scenario tag is still excluded by the heuristic entity facet;
  - caller-supplied `entity_tags` stay a hard filter for every chunk, scenario studies included;
  - the other facets still apply to scenario studies.

  Exact chunk-id membership. Oracle: a hand-built four-document corpus, where each document's
  facet tags are chosen so its expected admit/exclude follows from the rule above by
  inspection.
- **C3**: The MCP tool `macro_synthesize_shock_vector` returns a body equal to the REST
  synthesize serialization of the same `SynthesisResult`, that is
  `api.models.synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
  diagnostics=syn.diagnostics)`, including the `synthesis_diagnostics` key. With a scripted
  model that emits `{"shock_vector": {}}` over retrieved chunks,
  `synthesis_diagnostics.outcome == "llm_empty"` and `coverage_gap is True`. With one valid
  field citing a dated retrieved chunk, `outcome == "grounded"` and `coverage_gap is False`.
  Exact equality. Oracle: `SynthesisDiagnostics.outcome` semantics (documented in
  `synthesis/diagnostics.py`) and the REST handler's serialization.
- **C4**: `llm.sanitize.scrub_world_view_claims(text, world_view_id)` rewrites every
  world-view-id *claim* in `text` so the claimed value is the real id, and leaves every other
  character unchanged.
  - **A claim** is: the key `world_view_id` (case-insensitive, not preceded by a character in
    `[A-Za-z0-9_]`, optionally wrapped in a matching pair of `"`, `'` or `` ` ``; an unmatched
    quote is not part of the key), then optional spaces or tabs (never a newline), then `:` or
    `=`, then optional spaces or tabs, then a value.
  - **The value** is either a quoted string (`"…"`, `'…'` or `` `…` ``, possibly empty, not
    spanning a newline; the quotes are kept and only the inner text is replaced) or an unquoted
    run of `[A-Za-z0-9_.-]` that does not end in `.` (so in `world_view_id: macro-x.` the value
    is `macro-x` and the full stop is kept). A `:` / `=` followed by neither form is not a
    claim.
  - **The replacement value** is `world_view_id`, or the literal `unavailable` when
    `world_view_id` is `None`.
  - Mentions of the key without a `:` / `=` value (e.g. "pin results to the world_view_id")
    are not claims and are left alone.
  - The function is idempotent, and is the identity on text containing no
    `world_view_id` (case-insensitive).

  `QueryPipeline.synthesize` applies it, with the response's own `world_view_id`, to
  `query.answer.summary` and every `query.answer.details[].text` before returning. A
  fabricated id in the prose therefore never reaches a REST or MCP synthesize response.
  Exact string equality. Oracle: the grammar above applied by hand; the real id comes from a
  fixture manifest written with `utils.manifest.build_manifest` / `write_manifest`.

## Interface

- `llm.sanitize.scrub_world_view_claims(text: str, world_view_id: str | None) -> str` — new.
- `retriever.analysis.extract_filters(query: str, base: MetaFilter | None = None) -> MetaFilter`
  — existing, behaviour changes (C1).
- `pipelines.query_pipeline.QueryPipeline.synthesize(...)` — existing signature, behaviour
  changes (C2, C4).
- `mcp_server.server.build_mcp(...)` tool `macro_synthesize_shock_vector` — existing, response
  gains `synthesis_diagnostics` (C3).

## Notes

- All fixtures are synthetic: a hand-built corpus in the in-memory store with the hashing
  embedder, the identity reranker and a scripted `FakeLLM`, plus a fixture manifest under
  `tmp_path`. No `data/`, no network, no current date.
- C2's corpus mirrors the real tags (`configs/ingest/tier7_scenarios.yaml`):
  - an EBA-like study: factor `[cross_asset_scenario, stress_scenario, geopolitical_risk,
    equity_vol, fx]`, entity `[]`;
  - a GFSR-like study: factor `[geopolitical_risk, cross_asset_scenario, equity_vol,
    china_taiwan_tail]`, entity `[china]`;
  - a Taiwan/semis paper: entity `[china, semiconductors]`;
  - a non-scenario distractor: factor `[equity_vol]`, entity `[]`. It must stay excluded.
- C3 needs the `mcp` package. CI installs `requirements-dev.txt`, which must pin
  `mcp==1.12.4` (the version in the py312 env) before this lock merges; otherwise CI cannot
  import `mcp_server.server`.
- Live check after implementation (not a locked test): re-run the Taiwan question through
  `macro_synthesize_shock_vector` and confirm that EBA/GFSR chunks appear in `chunks[]`.
