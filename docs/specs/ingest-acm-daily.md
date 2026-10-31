# ingest-acm-daily — the NY Fed ACM 10-year term premium as a daily series

## Requirement

faultline drives its `rate_capital` and `real_rate_repricing` scenarios with the **ACM** 10-year
term premium (`tp:ACMTP10`, Adrian–Crump–Moench, NY Fed). MacroRAG stores only the
**Kim–Wright** term premium (`THREEFYTP10`, from FRED). That is a different model, so its moves
cannot stand in for ACM's.

The coming vocabulary extension adds a `TERM_PREMIUM_10Y` shock field defined as ACM. This spec
supplies the data: the NY Fed's daily ACM file, stored like the FRED daily series (one numeric
document per series per year) so the market-episode cards can measure it.

The source, checked on 2026-10-15: `https://www.newyorkfed.org/medialibrary/media/research/
data_indicators/ACMTermPremium.xls`, sheet `ACM Daily`, a `DATE` column (`dd-Mon-yyyy`, e.g.
`14-Oct-2026`) and one column per series (`ACMTP10` = 10-year term premium, percent), from
1961-06-14. One download holds the whole history; there is no rolling window.

Out of scope: the shock field and the cards (the vocabulary spec); other ACM columns (yields,
other maturities); the monthly sheet.

## Acceptance criteria

- **C1**: `ingest.usstats.parsers.parse_acm_daily(grid, series)`.
  - **Input:** `grid` is a sheet as rows of cell values; `series` is a list of column names.
  - **Header:** the first row whose first cell is `"DATE"`.
  - **Returns,** for each name in `series` in the given order, the rows below the header in date
    order: `{"source": "NYFED_ACM", "series_code": s, "date": <ISO date>, "value": float,
    "units": "percent"}`.
  - **Dates:** a cell that is a `dd-Mon-yyyy` string (e.g. `14-Oct-2026`) or a `date`/`datetime`
    value; a row whose date cell is anything else is skipped.
  - **Values:** a value that is empty, non-numeric or not finite is skipped.
  - **Errors:** a grid with no header, or a requested name missing from the header, raises
    `ValueError`.

  Exact equality, floats to `rel=1e-12`. Oracle: hand-written grids.
- **C2**: `AcmDailyHandler` (tier 3, `ingest.usstats.handlers.acm`):
  - `discover` returns one item, the endpoint's `discovery.url`.
  - `fetch` makes one GET of that URL with `use_cache=False`, reads the workbook bytes, takes the
    sheet named by `content.sheet`, and parses `content.series` with C1.
  - It keeps rows dated on or after a start date, which is the endpoint's
    `content.backfill_start` when `ctx.refresh` is true (a backfill run), else
    `f"{D.year − 1}-01-01"` (a routine run, `D` = `ctx.today`).
  - It emits one numeric document per (series, calendar year) present:
    - `doc_id = f"acm_daily_{s.lower()}_{Y}"`;
    - `doc_type = "acm_daily_series"`;
    - `url = f"acm://{s}/{Y}"`;
    - `release_date` = that year's latest date;
    - `timeseries` = that year's rows in date order;
    - no chunks.
  - **Failures:** a non-200 response, an unreadable workbook, a missing sheet, or a C1
    `ValueError` emits nothing.

  Exact equality. Oracle: an `.xlsx` workbook built in the test, served by a fake HTTP client,
  mapped by hand.
- **C3**: `configs/ingest/tier3_usstats.yaml` has an endpoint `acm_daily`:
  - handler `acm`;
  - `discovery.url` = the NY Fed URL above;
  - `content.sheet: "ACM Daily"`;
  - `content.series: ["ACMTP10"]`;
  - `content.backfill_start: "2005-01-01"`;
  - publisher `Federal Reserve Bank of New York`;
  - listed before `market_episodes`.

  The tier's handler registry resolves `acm` to `AcmDailyHandler`. Exact equality. Oracle: this
  list.

## Interface

- `ingest.usstats.parsers.parse_acm_daily(grid: list[list], series: list[str]) -> list[dict[str,
  Any]]` — new.
- `ingest.usstats.handlers.acm.AcmDailyHandler` — new class (base `UsStatsHandler`) with
  `discover` and `fetch`.

## Notes

- The workbook is read with `ingest.common.spreadsheet.read_workbook`, which handles `.xls` (the
  real file, via xlrd) and `.xlsx` (the test fixture, via openpyxl).
- Vintages: ACM is a model estimate and the NY Fed may revise history. A routine run rewrites only
  the current and previous year, as `fred_daily` does. A backfill rewrites every year from
  `backfill_start` and should be run deliberately.
- Fixtures: a workbook with sheets `ACM Monthly` (a decoy) and `ACM Daily`, built with openpyxl in
  the test; a fake HTTP client; fakes for cache and stores as in the ingest-fred-daily spec. No
  network, no `data/`, no clock.
