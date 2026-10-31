# RAG code rules (MacroRAG)

Writing-time digest of the review lenses `.claude/review/lenses/retrieval-synthesis.md` and
`macro-data-integrity.md`, which keep the detail and the failure modes reviewers hunt for.
MacroRAG's headline output is a citation-gated shock vector consumed by faultline.

- **LLM calls are bounded and accounted:** every provider call goes through `record_call` and
  the `resilience` retry/breaker (timeouts included), admission through `can_afford` /
  `CostGuard.check`. Every new loop, retry or fan-out has a bound. Model ids come from config,
  never literals. Vector-store calls have timeouts too.
- **Prompts are versioned files** under `prompts/`, resolved through the registry — never inline
  strings (they escape `prompt_hash` provenance).
- **Provenance:** every answer carries its gated `chunk_id`s; `world_view_id` stays a pure function
  of the corpus; cache keys change with corpus, prompt hash and model tier.
- **Retrieved text is untrusted:** it reaches prompts only through `format_context` /
  `fence_untrusted`; model-emitted field names, units and chunk ids are validated against
  `vocab.SHOCK_FIELDS` / the gated chunk set before use. No `eval`/`exec`.
- **Chunking is deterministic**; a chunking or payload change that keeps the document hash needs a
  rebuild plan.
- **Macro series:** units, scale and sign are explicit (percent vs bp vs fraction, `positive_means`
  conventions); periods are ISO and sortable; `release_date` is the publication date; reads are
  point-in-time; vintages are kept, never overwritten.
- **Storage and logs:** object data goes through `utils.objectstore`; no secrets or PII in logs.
