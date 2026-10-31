# episode-card-horizons — per-episode horizons for market-episode cards

## Requirement

Every market-episode card ([market-episodes](market-episodes.md), retired) is measured over the
same two horizons, 5 and 10 trading days. Four of faultline's scenarios replay windows that no card
covers, so the horizon check drops every field and the scenario stops with `macro_unavailable`:

| faultline scenario | window | `horizon_days` |
|---|---|---|
| `ai_deflation` | 2021-11-29 → 2022-10-14 | 221 |
| `inflation_repricing` | 2022-08-16 → 2022-10-14 | 42 |
| `ai_rotation` | 2024-07-10 → 2024-07-24 | 10 |
| `ai_efficiency_shock` | 2025-01-24 → 2025-01-27 | 1 |

This spec does three things:

1. An episode may set its own `horizons`, a list of trading-day counts. The default stays
   `[5, 10]`, so every card configured without `horizons` is byte-identical to today's.
2. A 1-trading-day line reads "1 trading day", not "1 trading days", and the card-value check
   still reads it.
3. Three new episodes are configured, and `deepseek` gains a 1-day horizon.

Everything else in the market-episodes Definitions and C1–C4 is unchanged, including the card
dating: a card's `release_date`, and every chunk's, is the latest `last_date` over all its lines.
So a card with a 221-day horizon is dated at the end of that window, for all its chunks.

The graduated market-episodes C5 property test (`test_property_committed_config_drives_the_handler`)
expects exactly a 5- and a 10-day chunk for every listed episode, `deepseek` included. In this spec
PR it is relaxed to sample only the listed episodes that configure no `horizons`, so `main` stays
green between this spec merging and its implementation merging. This spec pins `deepseek`'s card.

Out of scope: the horizon-validation rule (`synthesis.validate.check_source_horizon`); the card
dating rule; card text for horizons other than 1; any episode not listed in C4; re-indexing.

## Definitions

- **Horizons of an episode**: the episode dict's `horizons` value when the key is present, else
  `[5, 10]`.
- **Valid horizons**: a non-empty list of distinct `int`s, each ≥ 1; `bool` values are not `int`s
  here. The card uses them in **ascending order**, whatever their configured order.
- **Day word** for horizon `k`: `day` when `k == 1`, else `days`.
- **Chunk text** at horizon `k`: as in market-episodes, with `trading days` replaced by
  `trading {day word}` in the header and in every line:
  - header: `Market episode: {name}. Realized moves over the {k} trading {day word} after the
    {anchor} close, from FRED daily data.`;
  - line: `- {label}: {end} {unit} after {k} trading {day word} ({last_date}); largest move within
    the window {peak} {unit}`.

  For `k ≠ 1` this is exactly today's text.

## Acceptance criteria

- **C1**: `ingest.usstats.episodes.build_episode_document(episode, rows, today=D)` measures the
  episode at each of its horizons (ascending) instead of at 5 and 10:
  - one chunk per horizon with at least one move, in ascending horizon order, `seq` 0, 1, …;
  - each chunk's `metadata.horizon_days` is its horizon and its text is the chunk text above;
  - `fields["moves"]` has one key `str(k)` per horizon, each the
    `episode_moves(rows, anchor=a, horizon=k)` result (an empty dict when no series moves at `k`);
  - `release_date` (document and every chunk) is the latest `last_date` over all lines;
  - `content_hash` is the SHA-256 hex of the chunk texts joined by `"\n\n"`;
  - it returns `None` when no series moves at any horizon.

  An episode without `horizons` gives exactly the document it gives today, and an episode with
  `horizons: [5, 10]` gives the same document as one without the key. Floats to `rel=1e-9`, the
  rest exact. Oracle: the market-episodes Definitions and the Definitions above applied by hand.
- **C2**: `build_episode_document` raises `ValueError` when `horizons` is present and not valid
  (for example `[]`, `[0]`, `[-5]`, `[5, 5]`, `[5.0]`, `[True]`, `"5"`, `None`). Oracle: the
  Definitions.
- **C3**: `synthesis.validate.check_card_values` reads a 1-day card line. A field citing a
  `market_episode` chunk whose line for that field reads `… after 1 trading day (…); largest move
  within the window {peak} {unit}` is annotated `card_value_mismatch` when its value is more than
  the synth-card-value tolerance from `peak`, and left untouched when within it, exactly as for a
  `trading days` line. Oracle: the synth-card-value rules applied by hand to card text written in
  the test.
- **C4**: the `market_episodes` endpoint's `content.episodes` in
  `configs/ingest/tier3_usstats.yaml` holds exactly one entry for each id below, equal to it
  (tags in this order):

  | id | name | anchor | horizons | factor_tags | entity_tags |
  |---|---|---|---|---|---|
  | `ai_peak_to_trough_2022` | `2021-22 AI and growth-equity peak-to-trough` | 2021-11-29 | `[5, 10, 221]` | `equity_vol`, `credit_spread`, `discount_rate`, `ai_capex` | `ai`, `semiconductors` |
  | `hawkish_repricing_2022` | `Aug-Oct 2022 hawkish real-rate repricing` | 2022-08-16 | `[5, 10, 42]` | `inflation`, `discount_rate`, `rates_vol` | none |
  | `ai_rotation_2024` | `July 2024 rotation out of AI leaders` | 2024-07-10 | `[5, 10]` | `equity_vol`, `valuation_stretch` | `ai`, `semiconductors` |
  | `deepseek` | `DeepSeek AI selloff` | 2025-01-24 | `[1, 5, 10]` | `ai_capex`, `hyperscaler_concentration`, `valuation_stretch`, `equity_vol` | `ai`, `semiconductors`, `hyperscaler` |

  `ai_rotation_2024` may set `horizons: [5, 10]` or omit the key. No other episode sets `horizons`.
  Every tag is in `configs/ingest/vocabulary.yaml`; ids stay unique and anchors ascending; the
  previously configured ids keep their relative order. Exact equality. Oracle: this table.
- **C5**: the committed endpoint, loaded with `ingest.common.config.load_tier_config
  ("tier3_usstats")` and run by the handler the tier's registry resolves for it, emits for each C4
  episode one chunk per configured horizon. With a single synthetic VIXCLS series (`v_0` = 20.00 on
  the anchor and `20 + j` on anchor + `j` calendar days, `j` = 1 … max horizon), the chunk at
  horizon `k` has `horizon_days = k`, the line `- VIX (VIXCLS): +{k}.00 pts after {k} trading
  {day word} (anchor + k); largest move within the window +{k}.00 pts`, and the card's
  `release_date` is anchor + max horizon. Exact. Oracle: the Definitions by hand.

## Interface

None new. Existing, changed in behaviour only:
- `ingest.usstats.episodes.build_episode_document(episode: dict[str, Any], rows: list[dict[str,
  Any]], *, today: date) -> ParsedDocument | None` — reads `episode["horizons"]` (C1, C2).
- `synthesis.validate.check_card_values(fields, chunks, *, diag=None) -> None` — reads
  `trading day` lines (C3).
- `configs/ingest/tier3_usstats.yaml` — new episodes (C4).

## Notes

- **Anchors**, checked against the stored FRED data on 2026-10-18 (the stress shows after each):
  - `ai_peak_to_trough_2022`, 2021-11-29 (the last close before faultline's window): over 221
    trading days UST_2Y +394 bp, UST_10Y +250 bp, the 10-year real yield +259 bp, USDJPY +30.7%,
    VIX +9.5 pts, Baa−10Y +47 bp.
  - `hawkish_repricing_2022`, 2022-08-16: over 42 days UST_2Y +120 bp, UST_10Y +120 bp, the real
    yield +119 bp, VIX +12.3 pts, USDJPY +10.7%.
  - `ai_rotation_2024`, 2024-07-10, the close before the soft June CPI print of 2024-07-11: over 10
    days UST_2Y −25 bp, VIX +5.2 pts, USDJPY −5.2%.
  - `deepseek`, 2025-01-24: on 2025-01-27, UST_10Y −10 bp and VIX +3.05 pts.
- **Window ends.** Horizons count each series' own FRED rows. FRED's Treasury series skip bond
  holidays such as Columbus Day, so the 221- and 42-day windows end on 2022-10-17, one row after
  faultline's 2022-10-14. That is within the 3× horizon tolerance.
- **Missing fields.** ICE BofA spreads exist only from 2023-10-16, so the two 2022 cards have no
  HY_OAS line and their IG line is the BAA10Y stand-in, as for every card before `yen_carry`.
  Neither has MOVE; no card does.
- **Largest move.** The 221- and 42-day lines report both the end-of-window change and the largest
  move within the window, as every card line does. `check_card_values` makes a field citing a card
  carry the largest move.
- **Considered and rejected.** Adding the window's end date to the chunk header: every line
  already ends with its `last_date`, and the header change would rewrite the text of every existing
  card, three graduated specs' exact-text tests included.
- Fixtures: hand-written rows; a temporary directory plus the local object store for the handler,
  with `MACRORAG_DEPLOY_MODE` unset. No network, no `data/`, no clock.
