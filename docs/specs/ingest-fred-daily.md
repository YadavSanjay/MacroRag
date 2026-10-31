# ingest-fred-daily — ingest daily FRED market series without losing history

## Requirement

MacroRAG must ingest the daily FRED series the market-episode library needs (design note
`Macro_RAG_Portfolio_Coverage_202610.md` §4.1) into the time-series store. The stored history
must survive FRED's rolling windows:
- FRED serves the ICE BofA spread series only for the last three years (checked 2026-10-14:
  `BAMLC0A0CM` and `BAMLH0A0HYM2` start 2023-10-16).
- Re-fetching must never replace stored observations with a shorter history.

The API key must never reach a stored document, raw file name or log line.

Out of scope: computing episode moves (spec `market-episodes`); chunking or embedding (these
documents are numeric only); series beyond the list in C4.

## Definitions

- **Response**: the JSON body of `GET https://api.stlouisfed.org/fred/series/observations`, an
  object whose `observations` is a list of `{"date": "YYYY-MM-DD", "value": "<number>|."}`.
- **Row**: `{"source": "FRED", "series_code": <id>, "date": <YYYY-MM-DD>, "value": <float>,
  "units": <units>}`.
- **Partition** `(s, Y)`: the rows of series `s` whose date falls in calendar year `Y`.
- **Today** `D`: `ctx.today`. **Routine run**: `ctx.refresh` is `False`. **Backfill run**:
  `ctx.refresh` is `True`.
- **Old partition**: a partition with `Y < D.year − 1`.
- **Complete at its start**: an old partition is complete at its start when **either**
  - the parsed rows of the same response include a row dated before `Y-01-01` (so the source
    window did not cut into `Y`); **or**
  - `Y` is the year of `content.backfill_start` and the partition's earliest date is on or before
    `Y-01-10`. This year can have no earlier row in the response, because the request starts
    there.
- **Window-cut partition**: an old partition that is not complete at its start. A gap inside a
  series is not a cut: what counts is whether any earlier row exists, not how late `Y` starts.

  (Amended 2026-10-14 by re-lock. The first version called an old partition truncated when it
  started after `Y-01-10`. `tdd verify` showed that rule lets a cut inside the first ten days of
  a year rewrite that year shorter, which breaks C3. Dispute:
  `.claude/tdd/disputes/ingest-fred-daily.md` on `impl/ingest-fred-daily`.)

## Acceptance criteria

- **C1**: `ingest.usstats.parsers.parse_fred_observations(series_id, units, body)` returns
  the response's rows sorted by date ascending, one per observation:
  - an observation whose `value` is `"."`, empty or not a finite number is skipped;
  - `value` is the parsed float;
  - `date` is passed through when it is a valid ISO date, else the observation is skipped.

  A body that isn't JSON, or has no `observations` list, raises `ValueError`.
  Exact equality; floats to `rel=1e-12` (string to float). Oracle: hand-written response bodies
  mapped by hand.
- **C2**: `FredDailyHandler` (tier 3, `ingest.usstats.handlers.fred`) runs one request per
  configured series.
  - **Request parameters:** `series_id`, `file_type=json` and `api_key` (the secret named by
    the endpoint's `content.api_key_env`), plus `observation_start`:
    - in a routine run: `f"{D.year − 1}-01-01"`;
    - in a backfill run: the endpoint's `content.backfill_start`.
  - **Documents:** it emits one numeric document per partition present in the parsed rows, with:
    - `doc_id = f"fred_daily_{s.lower()}_{Y}"`;
    - `doc_type = "fred_daily_series"`;
    - `url = f"fred://{s}/{Y}"`;
    - `release_date` = the partition's latest date;
    - `timeseries` = the partition's rows in date order;
    - no chunks.

    It skips a window-cut partition.
  - **Failures:** a series whose request fails (non-200) or whose body raises in C1 emits
    nothing, and the other series still run.

  The API key's value appears in no emitted document (its full JSON) and no raw-store file name.
  Exact equality. Oracle: a fake HTTP client returning hand-written bodies, mapped by hand.
- **C3**: Re-fetching never shortens stored history.
  - A routine run requests no year before `D.year − 1`, so it emits no partition before it.
  - A backfill run emits an old partition only when it is complete at its start.

  Metamorphic oracle: run the handler on a response, then on the same response with every
  observation before some cut date removed. No partition emitted by the second run has fewer rows
  than the same partition from the first run, except partitions with `Y ≥ D.year − 1` and the
  partition of the `backfill_start` year.
- **C4**: `configs/ingest/tier3_usstats.yaml` has an endpoint `fred_daily`:
  - handler `fred`;
  - `content.api_key_env: FRED_API_KEY`;
  - `content.backfill_start: "2005-01-01"`;
  - a `content.series` list of exactly these `id: units` pairs:
    - `VIXCLS: index_points`
    - `BAMLC0A0CM: percent`
    - `BAMLH0A0HYM2: percent`
    - `BAA10Y: percent`
    - `DGS2: percent`
    - `DGS5: percent`
    - `DGS10: percent`
    - `DGS30: percent`
    - `DFII10: percent`
    - `THREEFYTP10: percent`
    - `SOFR: percent`
    - `DEXTAUS: twd_per_usd`
    - `DEXJPUS: jpy_per_usd`

  The tier's handler registry resolves `fred` to `FredDailyHandler`.
  Exact equality. Oracle: this list, copied from the design note's §4.1 table.

## Interface

- `ingest.usstats.parsers.parse_fred_observations(series_id: str, units: str, body: bytes) ->
  list[dict[str, Any]]` — new.
- `ingest.usstats.handlers.fred.FredDailyHandler` — new class (base `UsStatsHandler`), with
  `discover(self) -> list[DiscoveredItem]` and `fetch(self, item: DiscoveredItem) ->
  list[ParsedDocument]`.

## Notes

- Fixtures: hand-written JSON bodies; a fake client, cache and raw store like
  `test/test_scenarios_ingest.py`; a fixed `today`. No network, no `data/`, no real key (a
  sentinel string such as `"SENTINEL-KEY-123"` set in the environment by the fixture).
- Coverage verified live on 2026-10-14. From 2005: VIXCLS, DGS*, DEXTAUS, DEXJPUS, DFII10,
  BAA10Y, THREEFYTP10. From 2023-10-16: the ICE BofA OAS pair. From 2018-04-03: SOFR.
- `BAA10Y` (Moody's Baa yield minus the 10-year Treasury) is a long-history stand-in for IG
  spreads before the ICE window. It is a yield spread, not an option-adjusted spread, and the
  episode spec must label it so.
- Known limit: a closed year revised by FRED after its partition was written is only picked up
  by a backfill run. Daily market closes are not revised in practice.
