# synth-direction-prompt — tell the model each field's requested direction

## Requirement

Spec [synth-direction](synth-direction.md) drops a field whose sign contradicts the caller's
`synthesis.directions`. That is a filter: the model still never learns the direction, so it can
ground a field on an analog that moved the wrong way and lose the field, when a card that moved
the right way was in its context (on 2026-10-14, the tariff card's USDTWD −2.32% was used while
the Russia–Ukraine card's +1.97% was available).

When `directions` are given, the synthesize prompt's CONSTRAINTS section states them, so the
model picks evidence that moved the requested way. The check from synth-direction still runs
afterwards. Without `directions`, the rendered prompt is unchanged.

The `directions` argument of `synthesis.synthesizer.synthesize_shock_vector` landed inert in the
chore PR before this spec.

Out of scope: the template's other wording (an instruction paragraph and the version bump to
v4 ship in the implementation PR); the check itself (synth-direction).

## Definitions

- **Directions line** for directions `d` (field → `"up"`|`"down"`):
  `"- Expected directions (from the scenario; ground each field on evidence that moved this way): "`
  followed by the entries joined by `"; "`, then `"."`. Entries:
  - one per field of `d` whose upper-cased name is in `synthesis.vocab.SHOCK_FIELDS`, in
    `SHOCK_FIELDS` order;
  - entry = `f"{NAME} {dir} ({sign}, positive means {positive_means})"`, where `sign` is `"+"`
    for `"up"` and `"-"` for `"down"`, and `positive_means` is that field's
    `SHOCK_FIELDS[NAME].positive_means`.

  Names not in `SHOCK_FIELDS` and values other than `"up"`/`"down"` are skipped. With no entry
  left, there is no line.
- **CONSTRAINTS block**: the text that replaces `{constraints}` in the synthesize template. Today
  it is the horizon line (if `horizon_days`), then the fields line (if `fields`), joined by
  `"\n"`, or `"(none)"` if neither applies.

## Acceptance criteria

- **C1**: `synthesize_shock_vector(..., directions=d)` renders the CONSTRAINTS block as today's
  lines followed by the directions line, joined by `"\n"`; with only the directions line, the
  block is that line. With `d` `None`, empty, or with no valid entry, the block is exactly
  today's. The rest of the user prompt is unchanged. Oracle: the Definitions by hand; the user
  prompt is read from a scripted FakeLLM.
- **C2**: `QueryPipeline.synthesize(..., directions=d)` passes `d` (as validated by the caller)
  to `synthesize_shock_vector`, so the prompt the model receives carries the directions line;
  without `directions` the prompt has no directions line. Oracle: C1, read from the scripted
  FakeLLM's received prompt.

## Interface

- `synthesis.synthesizer.direction_line(directions: dict[str, str] | None) -> str | None` —
  new: the directions line, or None when there is none.
- `synthesis.synthesizer.synthesize_shock_vector(..., directions=...)` — existing (chore PR),
  becomes live.

## Notes

- Fixtures: a scripted FakeLLM that records `user` and returns an empty shock vector; one
  dated chunk; for C2, the in-memory pipeline harness of the synth-direction spec. No network,
  no `data/`, no clock.
- The C1 oracle for "today's block" is the existing rendering rule above, written out in the
  test; it does not call `_constraints_block`.
