# episodes-rates-selloff — market-episode cards for Treasury sell-offs

## Requirement

All ten market-episode cards are flight-to-quality rallies. Over 10 trading days:
- the largest move falls in every Treasury tenor for eight of them;
- tariff and Volmageddon raise only the long end, with 2Y falling.

A sell-off scenario therefore has no correct-direction evidence. faultline's `rate_capital`
("term-premium repricing in which Treasury yields rise and the curve bear-steepens") grounded
on Lehman's end values in one run and on prose in another. With `synthesis.directions`, every
falling tenor is now dropped as `direction_mismatch`, so the scenario comes back empty or partial.

Add three sell-off episodes, checked against the stored FRED data on 2026-10-15 (largest move
within 10 trading days, bp):

| id | anchor | 2Y | 5Y | 10Y | 30Y | term premium | story |
|---|---|---|---|---|---|---|---|
| `taper_tantrum` | 2013-06-18 | +16 | +42 | +40 | +26 | +23 | Fed taper signal; real yield +50 |
| `cpi_shock_2022` | 2022-06-09 | +62 | +54 | +45 | +27 | +17 | hot CPI; bear flattener |
| `treasury_refunding_2023` | 2023-07-31 | −14 | +18 | +23 | +30 | +10 | issuance and Fitch downgrade; long-end steepener |

The cards are built by the existing handler under the market-episodes Definitions. This change
is configuration plus the tests that pin it.

The graduated `episode-russia-ukraine` C1 test pinned "removing `russia_ukraine` leaves exactly
the nine". In this spec PR it is relaxed to "the nine keep their relative order", so `main` stays
green until the implementation adds the entries.

Out of scope:
- the card format and handler;
- other candidates: September 2022 CPI duplicates June 2022, and October 2023 is small at 10
  days because most of its rise came before any clean anchor;
- MOVE (no daily series).

## Acceptance criteria

- **C1**: the `market_episodes` endpoint's `content.episodes` in
  `configs/ingest/tier3_usstats.yaml` holds exactly one entry for each of:

  | id | name | anchor | factor_tags (in order) |
  |---|---|---|---|
  | taper_tantrum | Taper tantrum | 2013-06-18 | forward_guidance, term_premium, rates_vol, discount_rate |
  | cpi_shock_2022 | June 2022 CPI shock | 2022-06-09 | inflation, discount_rate, rates_vol |
  | treasury_refunding_2023 | Treasury refunding and Fitch downgrade | 2023-07-31 | fiscal, term_premium, rates_vol |

  - every entry has `entity_tags: []`;
  - every tag is in `configs/ingest/vocabulary.yaml`;
  - the list stays in ascending anchor order with unique ids;
  - the ten existing ids keep their committed relative order.

  Exact equality. Oracle: this table.
- **C2**: the committed endpoint, loaded with `load_tier_config("tier3_usstats")` and run by the
  handler the registry resolves, emits for `taper_tantrum`, from the FRED rows in Notes, the card
  the market-episodes C2 rules give by hand:

  | series | 5 days (to 2013-06-25): end / largest | 10 days (to 2013-07-02): end / largest |
  |---|---|---|
  | VIXCLS → VIX | +1.86 / +3.88 pts | −0.17 / +3.88 pts |
  | DGS2 → UST_2Y | +16.0 / +16.0 bp | +7.0 / +16.0 bp |
  | DGS10 → UST_10Y | +40.0 / +40.0 bp | +28.0 / +40.0 bp |
  | THREEFYTP10 (no field) | +23.0 / +23.0 bp | +19.0 / +23.0 bp |

  `release_date` 2013-07-02; chunk `factor_tags` are the episode's plus `cross_asset_scenario`.
  Each of the three new episodes builds a card from synthetic rows around its anchor.
  Floats to `rel=1e-9`, the rest exact. Oracle: the market-episodes Definitions by hand.

## Interface

None new. Existing: `MarketEpisodeHandler`, `load_tier_config`.

## Notes

- **C2 rows** (FRED, public domain; values as published):

  | date | VIXCLS | DGS2 | DGS10 | THREEFYTP10 |
  |---|---|---|---|---|
  | 2013-06-18 | 16.61 | 0.27 | 2.20 | 0.13 |
  | 2013-06-19 | 16.64 | 0.31 | 2.33 | 0.19 |
  | 2013-06-20 | 20.49 | 0.33 | 2.41 | 0.24 |
  | 2013-06-21 | 18.90 | 0.38 | 2.52 | 0.30 |
  | 2013-06-24 | 20.11 | 0.42 | 2.57 | 0.34 |
  | 2013-06-25 | 18.47 | 0.43 | 2.60 | 0.36 |
  | 2013-06-26 | 17.21 | 0.39 | 2.55 | 0.35 |
  | 2013-06-27 | 16.86 | 0.36 | 2.49 | 0.32 |
  | 2013-06-28 | 16.86 | 0.36 | 2.52 | 0.33 |
  | 2013-07-01 | 16.37 | 0.34 | 2.50 | 0.32 |
  | 2013-07-02 | 16.44 | 0.34 | 2.48 | 0.32 |

- Anchors: 2013-06-18 is the close before the June 19 FOMC press conference; 2022-06-09 the close
  before the May CPI release; 2023-07-31 the close of the Treasury borrowing-estimate day, before
  the Fitch downgrade (Aug 1) and the refunding statement (Aug 2).
- No ICE OAS exists before 2023-10-16, so all three cards carry the Baa−10Y IG stand-in. In a
  rates sell-off it can fall as the 10Y rises; that is how the stand-in is defined, not an error.
- Fixtures: as in episode-russia-ukraine. No network, no `data/`, no clock.
