# market-episodes — realized-move episode cards from daily FRED data

## Requirement

MacroRAG must ground short-horizon shock fields on **measured** short-horizon moves.
- Today the only quantified analogs are research prose: a 9/11 week quoted in a Taiwan paper,
  quarterly DFAST paths. faultline asks for 10-trading-day shocks (`horizon_days` counts
  trading days in faultline; see `scenariogen/plausibility.py`).
- With prompt v3 the model declares the evidence horizon honestly, so those analogs are now
  dropped as `horizon_mismatch`, and every rates scenario returns `coverage_gap`.

This spec turns the daily FRED series stored by `ingest-fred-daily` into one retrievable
**episode card** per named stress episode. A card has two chunks: the realized moves over the 5
and the 10 trading days after the episode's anchor close. Each chunk carries an exact
`horizon_days`, so `synthesis.validate.check_source_horizon` rates a field grounded on it
exactly. Cards are tagged as cross-asset scenario studies, so the existing reserved slots bring
them into synthesis.

Out of scope:
- choosing which episode fits a scenario (the model chooses; the horizon and citation gates
  apply as usual);
- rescaling moves;
- series or episodes beyond the lists below;
- freshness: faultline's gate will still call 2008–2025 cards stale. Exempting them there is a
  later, separate decision.

## Definitions

- **Rows**: time-series rows as stored by `ingest-fred-daily`: `{"source": "FRED",
  "series_code": s, "date": "YYYY-MM-DD", "value": float, "units": u}`.
- **Episode**: a dict with:
  - `id` (str), `name` (str), `anchor` (`date`, the last close before the shock);
  - `factor_tags` (list[str]) and `entity_tags` (list[str]).
- **Series classes** (by the row's `units`):
  - `percent` → shock unit `bp`; change = `(v_k − v_0) × 100`; 1 decimal in text.
  - `index_points` → `pts`; change = `v_k − v_0`; 2 decimals.
  - any `*_per_usd` → `pct`; change = `(v_k / v_0 − 1) × 100`; 2 decimals.
  - Rows with other units are ignored.
- **Anchor value** `v_0` of series `s`: the value of its latest row dated on or before the
  anchor, provided that row is no more than 7 calendar days before the anchor. Otherwise `s` has
  no move for this episode.
- **Window** of `k` trading days: the first `k` rows of `s` dated strictly after the anchor, in
  date order. Each series uses its own row dates. If `s` has fewer than `k` such rows, it has no
  move at horizon `k`.
- **Move** of `s` at horizon `k`:
  - `end` = the change from `v_0` to the last window value;
  - `peak` = the window change with the largest absolute value (ties → the earliest);
  - `last_date` = the last window row's date.
- **Canonical order and shock field:**

  | # | series | shock field | label in text |
  |---|---|---|---|
  | 1 | VIXCLS | VIX | `VIX (VIXCLS)` |
  | 2 | DGS2 | UST_2Y | `UST_2Y (DGS2)` |
  | 3 | DGS5 | UST_5Y | `UST_5Y (DGS5)` |
  | 4 | DGS10 | UST_10Y | `UST_10Y (DGS10)` |
  | 5 | DGS30 | UST_30Y | `UST_30Y (DGS30)` |
  | 6 | BAMLC0A0CM | IG_OAS | `IG_OAS (BAMLC0A0CM)` |
  | 7 | BAMLH0A0HYM2 | HY_OAS | `HY_OAS (BAMLH0A0HYM2)` |
  | 8 | BAA10Y | IG_OAS **only when BAMLC0A0CM has no move at this horizon**, else none | stand-in: `IG_OAS stand-in (BAA10Y, Moody's Baa yield minus 10-year Treasury; a yield spread, not an OAS)`; otherwise `BAA10Y (Moody's Baa yield minus 10-year Treasury)` |
  | 9 | DEXTAUS | USDTWD | `USDTWD (DEXTAUS)` |
  | 10 | DEXJPUS | USDJPY | `USDJPY (DEXJPUS)` |
  | 11 | DFII10 | none | `DFII10 (10-year TIPS real yield)` |
  | 12 | THREEFYTP10 | none | `THREEFYTP10 (Kim-Wright 10-year term premium)` |
  | 13 | SOFR | none | `SOFR (secured overnight financing rate)` |

  Series not in this table are ignored.
- **Line** for a series with a move, with `d` decimals and unit `u`:
  `f"- {label}: {end:+.{d}f} {u} after {k} trading days ({last_date}); largest move within the
  window {peak:+.{d}f} {u}"`.
- **Chunk text** at horizon `k`: the header
  `f"Market episode: {name}. Realized moves over the {k} trading days after the {anchor} close,
  from FRED daily data."`, then one line per series with a move, in canonical order, joined by
  `"\n"`.

## Acceptance criteria

- **C1**: `ingest.usstats.episodes.episode_moves(rows, anchor=a, horizon=k)` returns a dict keyed
  by series code, holding only the canonical-table series with a move at `k`. Each value is
  `{"field": <shock field or None>, "unit": <bp|pts|pct>, "end": float, "peak": float,
  "last_date": str}`, following the Definitions, including the BAA10Y stand-in rule.
  Floats to `rel=1e-9`. Oracle: hand-computed changes on hand-written rows.
- **C2**: `ingest.usstats.episodes.build_episode_document(episode, rows, today=D)` returns
  `None` when no series has a move at either horizon. Otherwise it returns a `ParsedDocument`
  with:
  - **identity:** `doc_id = f"market_episode_{id}"`, `source_id = "market_episodes"`,
    `doc_type = "market_episode"`, `url = f"episode://{id}"`;
  - **provenance:** `publisher = "MacroRAG (derived from FRED)"`,
    `publication = f"Market episode — {name}"`, `licence = "derived_fred"`, `tier = 3`,
    `bucket = "C"`, `retrieval_date = D`;
  - **`release_date`:** the latest `last_date` over all its lines;
  - **`timeseries`:** none;
  - **`fields`:** `{"episode": id, "anchor": anchor.isoformat(), "moves": {"5": <C1 result at
    5>, "10": <C1 result at 10>}}`;
  - **chunks:** one chunk per horizon with at least one move, in order 5 then 10:
    - `chunk_id = f"{doc_id}::{seq:03d}"`, with `seq` 0 and 1 in that order;
    - `text` = the chunk text;
    - `metadata.horizon_days = k`;
    - `metadata.doc_type = "market_episode"`;
    - `metadata.release_date` = the document's `release_date`;
    - `metadata.factor_tags` = the episode's factor tags with `"cross_asset_scenario"` added
      if absent (order kept, the added tag last);
    - `metadata.entity_tags` = the episode's entity tags;
  - **`content_hash`:** the SHA-256 hex of the chunk texts joined by `"\n\n"`.

  Same inputs give an identical document.
  Exact equality, floats to `rel=1e-9`. Oracle: the Definitions applied by hand.
- **C3**:
  - **Reading:** `ingest.usstats.episodes.read_fred_rows(store)` returns the `timeseries` rows of
    every document under the key prefix `tier3/fred_daily/` ending `.json` in the given
    `utils.objectstore.ObjectStore`. If two rows share `(series_code, date)`, the one from the
    document with the later `retrieval_date` wins.
  - **The handler:** `MarketEpisodeHandler` (`ingest.usstats.handlers.episodes`):
    - `discover` returns one item per configured episode (`content.episodes`);
    - `fetch` reads rows with `read_fred_rows` from `object_store_from_env(ctx.doc_store.root,
      "processed/ingest")` and returns `[build_episode_document(...)]`, or `[]` when that is
      `None`;
    - it never makes an HTTP request.

  Oracle: FRED-shaped documents written to an in-memory store, or to a temporary directory for
  the handler, mapped by hand.
- **C4**: `utils.corpus` carries a chunk's `metadata.horizon_days` into the point payload as
  `horizon_days` when it is not `None`, and omits it otherwise, so ordinary documents are
  unchanged. As a result, for a field citing an episode chunk with `horizon_days = 10`:
  - `check_source_horizon` at `horizon_days = 10` and tolerance 3 adds no horizon code;
  - at `horizon_days = 60` it adds `horizon_mismatch` with `basis = "chunk"` and
    `evidence_days = [10]`.

  Oracle: the synth-analog-horizon rules applied by hand.
- **C5**: `configs/ingest/tier3_usstats.yaml` has an endpoint `market_episodes`:
  - handler `episodes`, listed after `fred_daily` so it runs on fresh data;
  - `content.episodes` holds exactly these `(id, name, anchor)` triples:

    | id | name | anchor |
    |---|---|---|
    | lehman | Lehman Brothers bankruptcy | 2008-09-12 |
    | us_downgrade | US sovereign downgrade | 2011-08-05 |
    | china_deval | China yuan devaluation | 2015-08-10 |
    | volmageddon | Volmageddon short-vol unwind | 2018-02-01 |
    | covid | COVID-19 market crash | 2020-02-21 |
    | svb | Silicon Valley Bank failure | 2023-03-08 |
    | yen_carry | Yen carry-trade unwind | 2024-07-31 |
    | deepseek | DeepSeek AI selloff | 2025-01-24 |
    | tariff | Liberation Day tariff shock | 2025-04-02 |

  - every episode's `factor_tags` and `entity_tags` are drawn from
    `configs/ingest/vocabulary.yaml`;
  - `deepseek` carries entity tags `ai` and `semiconductors`.

  The tier's registry resolves `episodes` to `MarketEpisodeHandler`.
  Exact equality. Oracle: this table.

## Interface

- `ingest.usstats.episodes.episode_moves(rows: list[dict[str, Any]], *, anchor: date, horizon:
  int) -> dict[str, dict[str, Any]]` — new.
- `ingest.usstats.episodes.build_episode_document(episode: dict[str, Any], rows: list[dict[str,
  Any]], *, today: date) -> ParsedDocument | None` — new.
- `ingest.usstats.episodes.read_fred_rows(store: ObjectStore) -> list[dict[str, Any]]` — new.
- `ingest.usstats.handlers.episodes.MarketEpisodeHandler` — new class (base `UsStatsHandler`)
  with `discover` and `fetch`.
- `utils.corpus` point payload — existing; gains `horizon_days` (C4).

## Notes

- Fixtures:
  - hand-written rows;
  - `utils.objectstore.InMemoryObjectStore` for C3 reading;
  - a temporary directory plus the local object store for the handler, with
    `MACRORAG_DEPLOY_MODE` unset;
  - the C4 corpus built like other spec tests'.

  No network, no `data/`, no clock.
- Anchors were checked on 2026-10-14 against the stored FRED data. In each case the stress
  shows in the days after the anchor close, e.g. VIX 21.5 → 52.3 by day 4 after 2025-04-02;
  USDJPY −4.3% by day 3 after 2024-07-31.
- ICE BofA spreads exist only from 2023-10-16, so the IG line is the BAA10Y stand-in for every
  episode before `yen_carry`. HY has no stand-in and is absent before then.
- `ChunkMetadata.horizon_days` already exists.
