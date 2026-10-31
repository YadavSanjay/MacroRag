# episode-card-fields — card_field covers every field an episode card carries

## Requirement

`synthesis_diagnostics.missing_fields` tells a caller, per requested field that is not in the
vector, whether a market-episode card could have supplied it (`card_field`). The set behind it,
`synthesis.diagnostics.EPISODE_CARD_FIELDS`, lists 9 fields. The card producer,
`ingest.usstats.episodes.CANONICAL`, maps series to 11 fields: it also writes
`REAL_YIELD_10Y` (DFII10) and `TERM_PREMIUM_10Y` (ACMTP10), added with the rates
decomposition (spec vocab-rates-decomp). So a missing `REAL_YIELD_10Y` or `TERM_PREMIUM_10Y`
is reported as `card_field: false`, which tells faultline no card could help when one could.

When this change is done:

- `card_field` is true for every shock field an episode card carries, which now includes
  `REAL_YIELD_10Y` and `TERM_PREMIUM_10Y`, and false for every other field (e.g. `MOVE`,
  `EQUITY_US`).
- The two lists cannot drift apart again: the card-field set is exactly the set of shock fields
  named in `ingest.usstats.episodes.CANONICAL` (entries mapped to `None` excluded).

## Acceptance criteria

- **C1**: `synthesis.diagnostics.missing_fields(requested, kept, dropped)` reports
  `card_field: true` for `REAL_YIELD_10Y` and `TERM_PREMIUM_10Y`, whatever the case of the
  request, both when the field was never emitted and when it was dropped, and keeps the
  existing answers: true for `VIX`, `UST_2Y`, `UST_5Y`, `UST_10Y`, `UST_30Y`, `IG_OAS`,
  `HY_OAS`, `USDTWD`, `USDJPY`; false for the vocabulary fields no card carries (`UST`,
  `MOVE`, `MOVE_1Y`, `MOVE_5Y`, `MOVE_10Y`, `MOVE_30Y`, `EQUITY_US`, `XCCY_BASIS`). Every other part of each entry (`field`, `reason`,
  `codes`, order) is unchanged. Oracle: Requirement and `episodes.CANONICAL`.
- **C2**: `synthesis.diagnostics.EPISODE_CARD_FIELDS` equals `{field for field, _ in
  ingest.usstats.episodes.CANONICAL.values() if field is not None}`, and every member is a
  field of the shock vocabulary (`synthesis.vocab.SHOCK_FIELDS`). Oracle: Requirement (the
  producer's own map is the source of truth).

## Interface

No new functions. Exercised as is: `synthesis.diagnostics.missing_fields`,
`synthesis.diagnostics.EPISODE_CARD_FIELDS`, `ingest.usstats.episodes.CANONICAL`,
`synthesis.vocab.SHOCK_FIELDS`.

## Notes

- The locked synth-vector-report tests keep their own 9-field copy of the set but only draw
  field names from those 9 and non-card fields, so they are unaffected.
- The vocabulary (`synthesis.vocab.SHOCK_FIELDS`, 19 fields) minus the 11 card fields is exactly
  the 8 non-card names listed in C1.
- Pure functions over committed code: no network, no `data/`, no clock.
