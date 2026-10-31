# synth-direction — a shock field's sign must agree with the scenario's requested direction

## Requirement

A synthesis caller describes a directional scenario (risk-off: TWD weakens, spreads widen,
yields fall), but MacroRAG never checks that an emitted field moves that way. On 2026-10-14,
`taiwan_geopolitical` returned `USDTWD = −2.32 pct` (TWD strengthening), copied from the
April-2025 tariff card, against a question that says USD/TWD rises. Earlier, `rate_capital`
grounded on the SVB card, a yield rally, for a sell-off scenario. Both shipped as grounded.

The caller may now state a direction per field in `synthesis.directions` (`"up"` = a positive
value under the field's `positive_means` convention in `synthesis/vocab.py`, `"down"` = a
negative one). A field whose sign contradicts its requested direction is flagged
`direction_mismatch`, a **hard** code: dropped when `synthesis.drop_invalid` is on, like
`horizon_mismatch`. A request without `directions` behaves exactly as today.

The request fields (`SynthesisOptions.directions`, `SynthesisSettings.direction_check`, the
`directions` parameter of `QueryPipeline.synthesize` and of the MCP tool) landed inert in the
chore PR before this spec.

Out of scope:
- telling the model the directions in the synthesize prompt (a separate prompt PR);
- inferring directions from the question text;
- any check of magnitude against direction (a zero value is not a contradiction);
- the CLI.

## Definitions

- **Directions**: a mapping of shock-field name → `"up"` | `"down"`.
- **Contradiction** for field `f` with value `v`: `directions[f] == "up"` and `v < 0`, or
  `directions[f] == "down"` and `v > 0`. `v == 0` never contradicts.

## Acceptance criteria

- **C1**: `synthesis.validate.check_direction(fields, directions, *, diag=None) -> None`
  annotates `fields` in place:
  - each field with a contradiction gains `"direction_mismatch"` in its `validation` once (not
    duplicated if already present), and, when `diag` is given, one
    `diag.warn("direction_mismatch", field=<name>, requested=<"up"|"down">, value=<v>)`;
  - fields absent from `directions`, fields without a contradiction, and names in `directions`
    that are not in `fields` cause nothing;
  - `directions` `None` or empty causes nothing;
  - it never removes a field and never changes a value.

  Oracle: the Definitions by hand.
- **C2**: `"direction_mismatch"` is in `synthesis.validate.HARD_VALIDATION_CODES`, so
  `validate_shock_vector(..., drop_invalid=True)` removes a field carrying it and records the
  drop in `diag.dropped` with `"direction_mismatch"` among its `codes`; with
  `drop_invalid=False` the field stays, annotated. Oracle: the existing hard-code drop rule.
- **C3**: `QueryPipeline.synthesize(query, ..., directions=d)` runs `check_direction` on the
  gated fields inside the numeric-validation pass, before `validate_shock_vector`, when
  `synthesis.direction_check` is true. With `numeric_validation` on, `horizon_days` omitted,
  and the scripted model emitting `USDTWD −2.32 pct`, `USDJPY −4.95 pct`, `IG_OAS +25 bp` and
  `VIX +30.82 pts` (all `direct`, citing one dated chunk whose text holds no number-and-unit
  quantity, so no other validation code applies), and
  `d = {"USDTWD": "up", "USDJPY": "down", "IG_OAS": "up", "VIX": "up"}`:
  - `drop_invalid` true: `USDTWD` is dropped; `USDJPY`, `IG_OAS`, `VIX` are kept with no
    direction code; the response's `synthesis_diagnostics.dropped` names `USDTWD`;
  - `drop_invalid` false: all four are kept, `USDTWD` carries `direction_mismatch` and
    `synthesis_diagnostics.validation` has exactly one `direction_mismatch` warning, for
    `USDTWD`;
  - `direction_check` false, or `directions` omitted: no field carries `direction_mismatch`
    and the shock vector equals the one returned without `directions`.

  Oracle: C1 and C2 applied by hand.
- **C4**: the surfaces pass `directions` through, with field names validated like `fields`:
  - `POST /query` with `mode: "synthesize"` passes `synthesis.directions` to
    `QueryPipeline.synthesize`, with keys upper-cased; a key that is not a shock field returns
    **400** naming it; a value other than `"up"`/`"down"` returns 422;
  - the MCP tool `macro_synthesize_shock_vector(directions=...)` passes them the same way; an
    unknown key or a value other than `"up"`/`"down"` makes the tool call fail with an error
    naming it (the tool raises `ValueError`, as it does for `fields`).

  Oracle: the `fields` validation rules (`api.models.validate_shock_fields`) and this list.

## Interface

- `synthesis.validate.check_direction(fields: dict[str, ShockField], directions: dict[str,
  str] | None, *, diag: SynthesisDiagnostics | None = None) -> None` — new.
- `synthesis.validate.HARD_VALIDATION_CODES` — existing; gains `"direction_mismatch"` (C2).
- `QueryPipeline.synthesize(..., directions=...)`, `SynthesisOptions.directions`,
  `SynthesisSettings.direction_check`, MCP `directions` — existing (chore PR), become live.

## Notes

- Fixtures: the pipeline harness used by the synth-analog-horizon spec tests (fake embedder,
  in-memory vector store, identity reranker, scripted LLM), with the four fields above cited
  to a dated chunk. The values are those of the 2026-10-14 live `taiwan_geopolitical` run.
  No network, no `data/`, no clock.
- `rate_capital` (SVB for a sell-off) is the second motivating case: with
  `directions = {"UST_2Y": "up", ...}`, the SVB card's −112bp is dropped.
- The impl must also pass `directions` into the escalated synthesis pass, so an escalated
  vector is checked the same way.
- Guide 05 (`SynthesisOptions`, the validation-code table and the consumer pattern) is updated
  in the implementation PR.
