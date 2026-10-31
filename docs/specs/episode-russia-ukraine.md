# episode-russia-ukraine — a market-episode card for the Russian invasion of Ukraine

## Requirement

The market-episode library ([market-episodes](market-episodes.md), retired) has no geopolitical
war episode. faultline's `taiwan_geopolitical` scenario therefore still grounds `VIX` and
`UST_10Y` on a 9/11 week quoted in a 2025 St. Louis Fed Taiwan paper. Add one episode,
`russia_ukraine`, so that a geopolitical scenario has a measured 5- and 10-trading-day card
to choose from.

The card is built by the existing `MarketEpisodeHandler` under the market-episodes
Definitions; nothing about how cards are computed changes. This change is configuration
(`configs/ingest/tier3_usstats.yaml`) plus the tests that pin it.

The graduated market-episodes C5 test pinned the episode list as *exactly* nine triples. That
is relaxed, in this spec PR, to *includes* those nine, so `main` stays green between this spec
merging and its implementation merging. This spec pins the tenth.

Out of scope: any other episode; any change to the card format, the handler, or the
market-episodes C1–C4 behaviour; faultline's freshness gate.

## Acceptance criteria

- **C1**: the `market_episodes` endpoint's `content.episodes` in
  `configs/ingest/tier3_usstats.yaml` holds exactly one entry with `id: russia_ukraine`, equal to:
  - `name`: `Russian invasion of Ukraine`;
  - `anchor`: 2022-02-23;
  - `factor_tags`: `["geopolitical_risk", "sanctions_regime", "flight_to_quality",
    "credit_spread"]`, in that order;
  - `entity_tags`: `[]`.

  Every tag is in `configs/ingest/vocabulary.yaml`. The episode list stays in ascending anchor
  order with unique ids, and removing `russia_ukraine` from it leaves the nine market-episodes
  ids in their committed order (`lehman`, `us_downgrade`, `china_deval`, `volmageddon`,
  `covid`, `svb`, `yen_carry`, `deepseek`, `tariff`). Exact equality. Oracle: this list.
- **C2**: the committed endpoint, loaded with `ingest.common.config.load_tier_config
  ("tier3_usstats")` and run by the handler that the tier's registry resolves for it,
  emits for `russia_ukraine` the card that the market-episodes C2 rules give by hand: for
  example, from the FRED rows in Notes, the moves, texts and release date below. Its chunks
  carry `factor_tags` `["geopolitical_risk", "sanctions_regime", "flight_to_quality",
  "credit_spread", "cross_asset_scenario"]` and no entity tags.
  Floats to `rel=1e-9`, the rest exact. Oracle: the market-episodes Definitions applied by
  hand to the rows in Notes.

  | series | field | 5 days (to 2022-03-02): end / largest | 10 days (to 2022-03-09): end / largest |
  |---|---|---|---|
  | VIXCLS | VIX | −0.28 / −3.43 pts | +1.43 / +5.43 pts |
  | DGS10 | UST_10Y | −13.0 / −27.0 bp | −5.0 / −27.0 bp |
  | BAA10Y | IG_OAS (stand-in) | +14.0 / +24.0 bp | +22.0 / +25.0 bp |
  | DEXTAUS | USDTWD | +0.65 / +0.97 pct | +1.97 / +1.97 pct |

  `release_date` 2022-03-09.

## Interface

None new. Existing: `ingest.usstats.handlers.episodes.MarketEpisodeHandler`,
`ingest.common.config.load_tier_config`.

## Notes

- **Anchor.** 2022-02-23 is the last US close before the invasion began early on 2022-02-24.
  Checked against the stored FRED data on 2026-10-14: after that close, Baa−10Y widens 22bp,
  the 10Y TIPS real yield falls 36bp and USD/TWD rises 1.97% over 10 trading days. VIX moves
  little, because it had already risen from 20 to 31 between 02-09 and 02-23. The card records
  that honestly. Moving the anchor into the run-up is rejected: that window also holds the
  2022-02-10 CPI surprise, a rates shock rather than a geopolitical one.
- **Tags.** No vocabulary entity tag fits (there is no `russia`), so `entity_tags` is empty.
- **C2 rows** (FRED, public domain; values as published, typed into the test):

  | date | VIXCLS | DGS10 | BAA10Y | DEXTAUS |
  |---|---|---|---|---|
  | 2022-02-23 | 31.02 | 1.99 | 2.10 | 27.86 |
  | 2022-02-24 | 30.32 | 1.96 | 2.24 | 28.13 |
  | 2022-02-25 | 27.59 | 1.97 | 2.20 | 27.98 |
  | 2022-02-28 | 30.15 | 1.83 | 2.34 | 28.04 |
  | 2022-03-01 | 33.32 | 1.72 | 2.25 | 28.02 |
  | 2022-03-02 | 30.74 | 1.86 | 2.24 | 28.04 |
  | 2022-03-03 | 30.48 | 1.86 | 2.24 | 28.06 |
  | 2022-03-04 | 31.98 | 1.74 | 2.31 | 28.16 |
  | 2022-03-07 | 36.45 | 1.78 | 2.32 | 28.25 |
  | 2022-03-08 | 35.13 | 1.86 | 2.35 | 28.36 |
  | 2022-03-09 | 32.45 | 1.94 | 2.32 | 28.41 |

  No `BAMLC0A0CM` rows exist in 2022 (the ICE window starts 2023-10-16), so BAA10Y is the
  IG stand-in.
- Fixtures: the rows above written as processed FRED documents to a temporary directory; the
  handler runs with fakes for HTTP, cache and stores, and `MACRORAG_DEPLOY_MODE` unset. No
  network, no `data/`, no clock.
