# synth-analog-horizon — flag shock fields whose evidence describes a move over the wrong horizon

## Requirement

A shock field is the cumulative move over the requested `horizon_days`. The validation pass must
flag a field whose evidence describes a move over a very different horizon, so a consumer does
not price a multi-quarter number as a 10-day shock.

Live case (faultline `rate_capital`, 2026-10-13, horizon 10 days): `IG_OAS = +440 bp` was taken
from the 2026 DFAST severely adverse scenario (`doc_type: dfast_scenarios`), a quarterly path
whose peak comes several quarters out. The UST legs came from a 3-month move in a TBAC
presentation. Neither describes a 10-day move.

The check follows the existing contract of `synthesis/validate.py`: **annotate, don't drop**.
`horizon_mismatch` is a hard code, removed only under `synthesis.drop_invalid`.

Out of scope: rescaling a move to the requested horizon (a mismatched field is flagged, never
rescaled); judging whether an analog episode is on-topic; reading horizons out of chunk text.

## Definitions

- **Requested horizon `H`**: the `horizon_days` passed to synthesis. When it is `None` or not
  positive, the check does nothing.
- **Tolerance `T`**: `synthesis.horizon_tolerance` (default 3.0).
- **Cited chunks** of a field: the chunks in `chunks` whose `chunk_id` the field cites. Ids that
  don't resolve are ignored.
- **Rating** of a cited chunk, in this order:
  1. **exact** `h`: its `payload["horizon_days"]`, when that is an `int` > 0 (`bool` excluded).
  2. **minimum** `m`: from its `payload["doc_type"]` in this table, when there's no exact rating.

     | `doc_type` | `m` (days) | Why |
     |---|---|---|
     | `dfast_scenarios` | 90 | quarterly supervisory path |
     | `dfast_scenario_data` | 90 | quarterly supervisory path |
     | `dfast_results` | 90 | quarterly supervisory path |
     | `eba_esrb_adverse_scenario` | 365 | annual supervisory path |

  3. otherwise **unrated**.
- **Compatible**: an exact rating `h` is compatible when `max(h/H, H/h) ≤ T`. A minimum rating
  `m` is compatible when `H·T ≥ m`. Both bounds are inclusive.
- **Declared horizon** `d`: the field's `source_horizon_days`, when it is an `int` > 0.

## Acceptance criteria

- **C1**: `synthesis.validate.check_source_horizon(fields, chunks, horizon_days=H,
  tolerance=T, diag=diag)` decides each field as follows:
  - **(a)** If at least one cited chunk is rated, the field is flagged `horizon_mismatch`
    exactly when **no** rated cited chunk is compatible. Unrated chunks and `d` are ignored.
  - **(b)** If no cited chunk is rated and `d` exists, the field is flagged `horizon_mismatch`
    exactly when `max(d/H, H/d) > T`.
  - **(c)** If no cited chunk is rated and there's no `d`, the field is flagged
    `horizon_unknown`.

  Each code is added once to the field's `validation` list, and the function records one
  `diag.warn(<code>, field=<name>, requested_days=H, basis=<basis>, evidence_days=<days>)`
  per flagged field.
  - `basis` is `"chunk"` in case (a), `"model"` in (b) and `"none"` in (c).
  - `evidence_days` is the sorted list of distinct `h` and `m` values of the rated cited chunks
    in (a), `[d]` in (b) and `[]` in (c).

  It applies to every field, direct or analog. It never drops a field. With `H` absent or ≤ 0 it
  changes nothing and records nothing.
  Exact code membership. Oracle: the definitions applied by hand. With `H = 10` and `T = 3`:
  - a `dfast_scenarios` chunk flags (30 < 90);
  - with `H = 30` the same chunk does not flag (90 ≥ 90);
  - an exact-10 chunk next to a `dfast_scenarios` chunk does not flag;
  - `d = 30` does not flag; `d = 31` flags; `d = 4` does not flag (10/4 = 2.5); `d = 3` flags;
  - no rating and no `d` gives `horizon_unknown`.
- **C2**: `synthesis.synthesizer.synthesize_shock_vector` sets each field's
  `source_horizon_days` from the model's per-field `source_horizon_days`:
  - an `int` > 0, or a numeric string or whole-valued float equal to one, is stored as that
    `int`;
  - anything else (absent, `None`, ≤ 0, non-numeric, non-whole, `bool`) is stored as `None`,
    and the field itself is kept.

  `ShockField.to_dict()` includes `"source_horizon_days"` exactly when it isn't `None`.
  Exact equality. Oracle: a scripted `FakeLLM` emitting each listed form, mapped by hand.
- **C3**: `QueryPipeline.synthesize` runs `check_source_horizon` over the gated fields when both
  `synthesis.numeric_validation` and `synthesis.horizon_check` are on. It runs before the
  existing `drop_invalid` pass, with `horizon_days` from the request and `tolerance` from
  `synthesis.horizon_tolerance`.
  - "The response" is `api.models.synthesis_to_dict` of the `SynthesisResult`.
  - The codes appear in `shock_vector[<f>].validation` and in `synthesis_diagnostics.validation`.
  - `horizon_mismatch` is **hard** (it joins `HARD_VALIDATION_CODES`). With
    `synthesis.drop_invalid = True` the field is absent from the shock vector, and
    `synthesis_diagnostics.dropped` has an entry for it with `stage == "validate"` whose `codes`
    include `horizon_mismatch`.
  - `horizon_unknown` is surface-only: the field is kept even with `drop_invalid`.
  - With `horizon_check = False`, with `numeric_validation = False`, or with no `horizon_days`,
    neither code appears.

  Exact membership. Oracle: a scripted `FakeLLM` that emits the live case (`IG_OAS` +440 bp citing
  a `dfast_scenarios` chunk, `horizon_days = 10`), applied to the rules above.

## Interface

- `synthesis.validate.check_source_horizon(fields: dict[str, ShockField], chunks:
  list[RetrievedChunk], *, horizon_days: int | None, tolerance: float, diag:
  SynthesisDiagnostics | None = None) -> None` — new. It annotates `fields` in place and never
  drops.
- `synthesis.synthesizer.synthesize_shock_vector(...)` — existing; parses `source_horizon_days`
  (C2).
- `synthesis.schema.ShockField.to_dict()` — existing; emits `source_horizon_days` (C2).
- `pipelines.query_pipeline.QueryPipeline.synthesize(...)` — existing; behaviour changes (C3).
- Already on `main` (inert, chore PR): `ShockField.source_horizon_days`,
  `SynthesisSettings.horizon_check` (default `True`) and `horizon_tolerance` (default `3.0`).

## Notes

- Fixtures are synthetic: `RetrievedChunk`s with dated payloads carrying `doc_type` and, where
  needed, `horizon_days`; `ShockField` instances; and for C2/C3 the in-memory pipeline with the
  hashing embedder and `FakeLLM`. No `data/`, network or clock.
- The `doc_type` table is data the implementation adds to `synthesis/vocab.py`. This spec fixes
  its contents.
- `payload["horizon_days"]` is how the planned market-episode cards (design note
  `Macro_RAG_Portfolio_Coverage_202610.md` §4.1) will carry an exact horizon. Today no ingested
  chunk sets it.
- The implementation also updates the synthesize prompt (a new prompt version) to ask for a
  per-field `source_horizon_days`. Prompt wording isn't locked; compliance is checked live.
- `faultline` reads `validation` codes only as data. Its contract gains `source_horizon_days` as an
  optional key, which it can ignore.
- Live check after implementation (not a locked test): rerun `rate_capital` with
  `horizon_days = 10`. `IG_OAS` from DFAST is dropped as `horizon_mismatch`. The UST legs are
  dropped if the model declares the TBAC move as about 90 days; record what it declares.
