# vocab-rates-decomp — REAL_YIELD_10Y and TERM_PREMIUM_10Y shock fields

## Requirement

faultline's `real_rate_repricing` scenario is driven by the 10Y TIPS real yield
(`real:DFII10`) and the ACM 10Y term premium (`tp:ACMTP10`). Its `rate_capital` narrative is a
term-premium repricing. Neither driver is among MacroRAG's 17 shock fields, so MacroRAG cannot
ground them, although the data is now stored:
- `DFII10` in `fred_daily`;
- the ACM `ACMTP10` in `acm_daily` (spec ingest-acm-daily).

Decision (user, 2026-10-15): add two fields to the contract:

| field | unit | positive means | evidence |
|---|---|---|---|
| `REAL_YIELD_10Y` | bp | the 10Y TIPS real yield rises | FRED `DFII10` |
| `TERM_PREMIUM_10Y` | bp | the ACM 10Y term premium rises | NY Fed `ACMTP10` (not Kim–Wright) |

The market-episode cards measure both, so a 10-day sell-off scenario can ground them. For
example, the taper tantrum card shows the real yield +50 bp at 5 days and the ACM term premium
about +26 bp.

The graduated market-episodes C1 property test pinned `DFII10 → field None`. In this spec PR it
changes to `REAL_YIELD_10Y`.

Out of scope:
- swap spread and CDS-bond basis (no OIS swap data);
- breakevens;
- faultline's mapping (its own change);
- Kim–Wright, which stays a label-only card line.

## Acceptance criteria

- **C1**: `synthesis.vocab.SHOCK_FIELDS` gains the two fields above: units `{"bp"}`, default unit
  `bp`, `positive_means` `"real yield rises (10Y TIPS)"` and `"term premium rises (ACM 10Y)"`.
  - `api.models.validate_shock_fields` accepts them;
  - `schema_dict()` lists them;
  - the other 17 fields are unchanged.

  Oracle: this table.
- **C2**: plausibility limits (spec synth-band-floor's rule): `magnitude_limit` gives
  `REAL_YIELD_10Y` a cap of 350 bp with floor 0.5, and `TERM_PREMIUM_10Y` a cap of 200 bp with
  floor 0.5. So at 10 days the limits are 175 and 100 bp; at 90 days, 350 and 200 bp. Oracle: the
  synth-band-floor Definitions by hand.
- **C3**: market-episode cards.
  - **Mapping:** `episode_moves` maps `DFII10` to `REAL_YIELD_10Y` (label
    `"REAL_YIELD_10Y (DFII10, 10-year TIPS real yield)"`) and `ACMTP10` to `TERM_PREMIUM_10Y`
    (label `"TERM_PREMIUM_10Y (ACMTP10, ACM 10-year term premium)"`).
  - **Canonical order:** `ACMTP10` comes right after `DFII10`. `THREEFYTP10` stays label-only
    (field `None`).
  - **Reading:** `ingest.usstats.episodes.read_episode_rows(store)` returns the rows of every
    `.json` document under `tier3/fred_daily/` and `tier3/acm_daily/`, with the same
    later-retrieval-wins rule as `read_fred_rows`.
  - **The handler** reads with it, so a card built from FRED and ACM documents carries both lines.

  Floats to `rel=1e-9`. Oracle: the market-episodes Definitions by hand on hand-written rows.

## Interface

- `ingest.usstats.episodes.read_episode_rows(store: ObjectStore) -> list[dict[str, Any]]` — new.
- `synthesis.vocab.SHOCK_FIELDS`, `synthesis.validate.MAGNITUDE_BOUNDS` /
  `MAGNITUDE_SCALE_FLOOR`, `ingest.usstats.episodes.CANONICAL` — existing; extended.

## Notes

- Contract change: guide 05's field table, README and `CLAUDE.md` move from 17 to 19 fields in
  the implementation PR. `GET /synthesis/schema` reflects the new fields automatically.
- faultline maps `REAL_YIELD_10Y → real:DFII10` and `TERM_PREMIUM_10Y → tp:ACMTP10` on its side.
- Rollout after merge: re-ingest `market_episodes`; the card texts change, so their content
  hashes do, and `index --incremental` re-embeds them.
- Fixtures: hand-written rows and documents in an in-memory store; the handler harness of the
  market-episodes spec. No network, no `data/`, no clock.
