---
name: macro-data-integrity
applies_to: ["src/ingest/**", "configs/ingest/**", "src/vectorstores/timeseries_store.py", "src/pipelines/hydrate.py", "src/eval/{episodes,selfcheck,shock_eval,shock_metrics}.py", "src/synthesis/{validate,vocab}.py", "src/retriever/ranking.py", "src/vectorstores/{filters,memory,qdrant_store}.py", "configs/eval/**", "scripts/{reextract_dfast,reextract_sep,rechunk_processed,normalize_vocab}.py"]
---
# Macro data and time-series integrity

The numbers MacroRAG stores and compares are exact (SQLite `observations`), and the dates it
attaches to prose chunks (`release_date`) are what every freshness, point-in-time and
citation check keys on. A wrong unit, period or date here corrupts the shock vector without
any test turning red.

## Hunt for

**Units and scale (most damaging)**
- Percent vs basis points vs fractions. Stored rate/yield series are in **percent**; shock
  fields in `bp` need `_PCT_TO_BP` (x100) in `validate._check_series_anchor`. A new
  field-to-series mapping (`SHOCK_FIELD_TO_SERIES`, `RECONCILE_FIELD_TO_SERIES`,
  `eval/episodes.DEFAULT_STORE_MAPPING` `scale`/`unit`) with a missing or doubled scale, or a
  `spread` kind whose two operands are in different units.
- `MOVE`/`MOVE_*` units (`rel` vs `pts`) and `EQUITY_US` as a **level** change in percent
  (`_relativise`), not vol. Sign convention drift against `vocab.SHOCK_FIELDS.positive_means`
  (yields rise, spreads widen, TWD/JPY depreciate = USDTWD/USDJPY up).
- `MAGNITUDE_BOUNDS` / `_MATERIAL_FLOOR` / `_TOL_FLOOR` keyed on a unit string that no longer
  matches `FieldSpec.units`: the check silently never fires (`.get(unit)` returns nothing).

**Period, date and ordering**
- `period` is stored as free text (`_coalesce(row, "period", "date", "time", "horizon")`) and
  compared **lexicographically** (`episodes._slice`, `_series` sort, `MetaFilter.date_from/
  date_to` against `str(release_date)` in `filters.match_payload` and the Qdrant filter). A
  handler that emits `2020Q1`, `2020-03`, `Mar-2020`, a bare year or `longer_run` next to
  `YYYY-Qn` / ISO dates sorts and slices wrongly with no error. `release_date` must be ISO
  `YYYY-MM-DD`; a missing one makes a chunk invisible to date filters and to the citation gate.
- Frequency mixing: monthly, quarterly and annual series joined or differenced without
  alignment. `_spread` aligns on period, so a position-based zip or an unaligned subtraction is
  a regression. `bea.py` carries `frequency`; resampling must state its rule (period-end vs
  mean) and keep the original frequency in provenance.
- Quarter arithmetic (`shift_quarters`, `quarter_start_date`, `point_in_time_cutoff`):
  off-by-one at year boundaries, or an inclusive/exclusive swap that lets the episode's own
  quarter into `pre` or the cutoff day into the PIT window.

**Point-in-time, vintages and revisions**
- Look-ahead: any retrieval, hydration or reference construction used for `ex_ante_skill`
  that is not bounded by `release_date` < episode start (`point_in_time_filter`). Series
  reference windows must also exclude post-start data when the mode claims ex-ante.
- Vintage collapse: DFAST/SEP/EIA rows carry `vintage` / `forecast_vintage` / `scenario`.
  A change that drops them from the row, the `observations` columns or the
  `TimeseriesStore.query` filters lets `prior` and `current` SEP, or two DFAST years, be
  averaged or overwritten. `hydrate._SOURCE_CUES` (`"sep"` is a substring cue and matches
  inside other words) and `_VARIABLE_CUES` order decide which series a question gets.
- Release-calendar alignment: `release_date` must be the **publication** date (via
  `ingest/common/dateextract.py`), not the reference period, retrieval date or file mtime.
  Falling back to `retrieval_date` makes a stale document look fresh to
  `ranking.apply_time_decay` and to the downstream freshness check.
- Seasonal adjustment and revisions: a new BLS/BEA/Census/EIA series must record whether it
  is SA/NSA and the vintage fetched. Mixing SA and NSA, or overwriting a series on re-ingest
  without keeping the vintage, makes realized-move references drift between runs.

**Store and ingest semantics**
- `TimeseriesStore.build(reset=True)` drops the table. Callers that build from a subset (a
  `--tier` or `--limit` ingest) would wipe the rest. `_as_float` returning `None` and
  consumers that skip `None` silently shorten a series (`_series`, `selfcheck`).
- Duplicate `(source, variable, scenario, period)` rows after re-ingest: `hydrate` and
  `_series` have no dedupe, so a duplicate double-counts in percentiles and spans.
- Controlled vocabulary: `factor_tags`/`entity_tags` come from `configs/ingest/
  vocabulary.yaml` (also written at runtime by `PUT /admin/vocabulary`). Free-text tags
  silently fail `MetaFilter` facet matches. `scripts/reextract_*` and `rechunk_processed`
  must preserve `content_hash` semantics, because incremental indexing diffs on it.

## Severity anchors

- P0: unit or scale error that changes a stored or emitted magnitude by a constant factor
  (percent read as bp); look-ahead in `ex_ante_skill` or a PIT filter; a sign flip in a shock
  field's convention reaching the vector.
- P1: period-format mix that mis-sorts or mis-slices; `release_date` sourced from
  retrieval/period date; vintage or scenario dropped from rows; a plausibility or anchor map
  that silently never fires; SA/NSA mixing in a realized reference.
- P2: missing dedupe; hydration cue that over-matches; tolerance constants without rationale;
  frequency not recorded in provenance.
- P3: naming and logging in handlers; a series mapping lacking a comment explaining its unit.

## Proof recipes

- Pure-function repros under scratch (no data needed), run with the profile python:
  `episodes.shift_quarters("2020-Q1", -1)`, `point_in_time_cutoff`, `_slice` with mixed period
  strings, `_spread` on offset series, `validate_shock_vector` with a `bp` field against a
  percent-unit `TimeseriesHit` (build with `utils.schema.TimeseriesHit`).
- In-memory store: `TimeseriesStore(tmp_path/"ts.db").build(rows)` with duplicate and
  mixed-format rows, then `hydrate_timeseries` / `derive_realized_reference` to show the
  distortion.
- `match_payload(MetaFilter(date_to="2020-12-31"), {"release_date": "2020-3"})` style probes
  for lexicographic date bugs.
- Pytest: `test/test_episode_backtest.py`, `test_shock_eval.py`, `test_selfcheck.py`,
  `test_hydrate.py`, `test_dateextract.py`, `test_narrative_dating.py`, `test_house_dating.py`,
  `test_synthesis.py` (exit code, not summary text).

## Not findings

- Wide `MAGNITUDE_BOUNDS` and `_OBSERVED_RANGE_FACTOR`; annotate-don't-drop behavior.
- Low corpus depth causing a high `ex_ante_skill_abstention_rate` (reported, not a defect).
- `shock_eval` scoring against projected official paths: it measures transcription fidelity,
  not forecast skill, by design.
- HY_OAS anchored to the BBB proxy in `SHOCK_FIELD_TO_SERIES` but excluded from
  `RECONCILE_FIELD_TO_SERIES` (deliberate: always `analog`).
- Playwright `_render` fallback and polite-fetch behavior in `handler_base`; live-source
  availability and real-data accuracy (cannot be checked in review).
