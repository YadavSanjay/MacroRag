# synth-card-value — a field grounded on an episode card must carry the card's largest move

## Requirement

Each market-episode card line gives two numbers per series: the move at the end of the window and
the **largest move within the window**. The synthesize prompt (v3+: "PEAK, NOT ENDPOINT")
asks for the largest move. With `synthesis.directions` sent, the model sometimes takes whichever
of the two matches the requested sign instead.

Live (2026-10-15, `rate_capital`, prompt v4, directions "up"): UST_2Y came back **+8 bp**. That is
the refunding card's 10-day *end* value; its largest move was **−14 bp**. So a field passed the
direction check on a number the card does not report as the shock. The same happened under the
rejected v5 prompt (Lehman +8/+11/+4 bp).

A field that cites a card measuring it must carry that card's largest move. Otherwise it gets
`card_value_mismatch`, a **hard** code (dropped under `drop_invalid`).

Out of scope:
- fields that cite no card, or only cards that do not measure the field;
- rescaling;
- choosing between the 5- and 10-day chunks (the cited chunk decides).

## Definitions

- **Card line** in a chunk whose payload `doc_type` is `market_episode`: a line of the form
  `"- {label}: {end} {unit} after {k} trading days ({date}); largest move within the window
  {peak} {unit}"`, where `end` and `peak` are signed decimals.
- **Field of a line**: the label's first token (the text before its first space), when that token
  is a name in `synthesis.vocab.SHOCK_FIELDS`. For example `UST_2Y (DGS2)` → `UST_2Y`, and
  `IG_OAS stand-in (BAA10Y, …)` → `IG_OAS`. Other lines (`BAA10Y …`, `THREEFYTP10 …`,
  `SOFR …`) have no field.
- **Tolerance** by unit: half the card's last printed decimal, plus 1e-9. That is 0.05 for
  `bp` (one decimal) and 0.005 for `pts` and `pct` (two decimals).
- **Mismatch** for a field `f` with value `v`: `f` cites at least one card chunk that has a line
  for `f`, and for every such line `|v − peak| > tolerance`.

## Acceptance criteria

- **C1**: `synthesis.validate.check_card_values(fields, chunks, *, diag=None) -> None` annotates
  each field with a mismatch with `"card_value_mismatch"` once. With `diag`, it records
  `diag.warn("card_value_mismatch", field=f, value=v, card_peaks={chunk_id: peak, …})`, covering
  each cited card line for `f`. It never drops or changes a value. Fields without a cited card
  line for themselves are untouched. Oracle: the Definitions by hand on card texts written in the
  test.
- **C2**: `"card_value_mismatch"` is in `HARD_VALIDATION_CODES`, so `drop_invalid` removes it.
  Oracle: the existing hard-code rule.
- **C3**: `QueryPipeline.synthesize` runs `check_card_values` in the numeric-validation pass, before
  `validate_shock_vector`. In the live case rebuilt from the scripted model (UST_2Y +8.0,
  UST_10Y +23.0, IG_OAS +16.0, all citing the refunding card's 10-day chunk, whose largest moves
  are −14.0, +23.0, +16.0):
  - with `drop_invalid`, UST_2Y is dropped and the other two are kept;
  - `missing_fields` (when UST_2Y is requested) reports it as dropped with
    `card_value_mismatch`.

  Oracle: C1 and C2 by hand.

## Interface

- `synthesis.validate.check_card_values(fields: dict[str, ShockField], chunks:
  list[RetrievedChunk], *, diag: SynthesisDiagnostics | None = None) -> None` — new.
- `HARD_VALIDATION_CODES` — existing; gains `card_value_mismatch` (C2).

## Notes

- Fixtures: card texts in the exact market-episodes format (the refunding card's 10-day UST and IG
  lines, values as stored on 2026-10-15), hand-built fields; for C3 the pipeline harness of the
  synth-direction spec. No network, no `data/`, no clock.
- Guide 05's validation-code table gains the code in the implementation PR.
