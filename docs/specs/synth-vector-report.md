# synth-vector-report — say why a requested field is missing, and when a vector mixes episodes

## Requirement

faultline's 2026-10-15 dual-RAG run (`8da52756`) asked for fields the vector did not return, and
could not tell why:
- `rate_capital` got UST_10Y/30Y from the tariff card but not UST_2Y/5Y. The card measures both,
  but they moved down (largest move −23 bp each) in a sell-off scenario.
- Its MOVE was dropped as `horizon_mismatch`; no episode card measures MOVE.
- `ai_bust` got no UST_2Y, although the DeepSeek card's 2Y fits.

The answer was in `synthesis_diagnostics.dropped` for some fields and nowhere for others.

The same run's `taiwan_geopolitical` vector took its fields from three unrelated episodes (tariff,
Russia–Ukraine, US downgrade). The resulting curve shape is one no episode produced, and nothing
in the response says so.

This spec adds two deterministic, additive reports:
- `synthesis_diagnostics.missing_fields`: for each field the caller requested in
  `synthesis.fields` but did not get, why (dropped, with codes, or never emitted) and whether any
  episode card can measure that field at all;
- a soft validation code `episode_mixed` on every kept field that cites an episode card, when the
  kept fields cite more than one episode, with a diagnostics warning mapping episode → fields.

Neither drops a field or changes a value.

Out of scope: choosing or enforcing a single episode (a prompt change, separately); filling in
omitted fields from a card; MOVE data.

## Definitions

- **Card fields**: the shock fields an episode card can carry: `VIX`, `UST_2Y`, `UST_5Y`,
  `UST_10Y`, `UST_30Y`, `IG_OAS`, `HY_OAS`, `USDTWD`, `USDJPY` (the canonical table of spec
  market-episodes).
- **Episode of a chunk**: for a retrieved chunk whose payload `doc_type` is `"market_episode"`,
  the part of its `chunk_id` before `"::"` (e.g. `market_episode_tariff`). Other chunks have no
  episode.
- **Episodes of a field**: the set of episodes of the chunks the field cites that are present in
  the retrieved chunks.

## Acceptance criteria

- **C1**: `synthesis.diagnostics.missing_fields(requested, kept, dropped) -> list[dict]`, given
  the requested field names (any case), the kept field names, and the `dropped` records
  (`{"field", "stage", "reason", ...}`), returns one entry per requested name that is not kept,
  in requested order, upper-cased, without duplicates:
  - `{"field": F, "reason": "dropped", "codes": C, "card_field": B}` when `dropped` has a record
    for `F`. `C` lists, in record order, each record's `codes` entries when present, else its
    `reason`, without duplicates.
  - `{"field": F, "reason": "not_emitted", "codes": [], "card_field": B}` otherwise.
  - `B` is whether `F` is a card field.

  `requested` `None` or empty gives `[]`. Oracle: the rules by hand.
- **C2**: `synthesis.validate.check_episode_mix(fields, chunks, *, diag=None) -> None`: when the
  union of the kept fields' episodes has two or more members:
  - every field with at least one episode gains `"episode_mixed"` in `validation` once;
  - with `diag`, it records one `diag.warn("episode_mixed", episodes=E)`, where `E` maps each
    episode to the sorted names of the fields citing it.

  With zero or one episode, nothing changes. It never drops a field or changes a value.
  `"episode_mixed"` is not in `HARD_VALIDATION_CODES`. Oracle: the Definitions by hand.
- **C3**: `QueryPipeline.synthesize` reports both:
  - `synthesis_diagnostics.missing_fields` in the response is C1 applied to the request's
    `fields` (or `[]` without them), the final kept fields, and the final `dropped` records;
  - inside the numeric-validation pass, after `validate_shock_vector`, it runs
    `check_episode_mix` on the kept fields, so a vector citing two episode cards carries
    `episode_mixed` on those fields and one `episode_mixed` warning.

  Oracle: C1 and C2 applied by hand to a scripted model over a corpus of two episode-card
  chunks and one prose chunk.

## Interface

- `synthesis.diagnostics.missing_fields(requested: list[str] | None, kept: list[str] |
  dict, dropped: list[dict]) -> list[dict]` — new.
- `synthesis.validate.check_episode_mix(fields: dict[str, ShockField], chunks:
  list[RetrievedChunk], *, diag: SynthesisDiagnostics | None = None) -> None` — new.
- `SynthesisDiagnostics.as_dict()` — existing; gains `missing_fields` (C3).

## Notes

- Fixtures: hand-built `ShockField`s and `RetrievedChunk`s; for C3 the in-memory pipeline
  harness of the synth-direction spec, with two `market_episode` documents and one research
  note. No network, no `data/`, no clock.
- `card_field` answers faultline's MOVE question deterministically: MOVE is never a card field,
  so a missing MOVE comes only from prose or not at all.
- Guide 05 documents `missing_fields` and `episode_mixed` in the implementation PR.
