# synth-band-floor — short-horizon plausibility limits must admit realized moves

## Requirement

`validate_shock_vector` flags `implausible_magnitude` (a hard code, dropped under
`drop_invalid`) when `|value|` exceeds the field's limit. The limit is the field's cap in
`MAGNITUDE_BOUNDS`, set for about one quarter (`validation_reference_horizon_days`, 90), times
`min(√(horizon_days / reference), 3)`. At 5 and 10 days that factor is 0.24 and 0.33.

Volatility and short-rate shocks jump; they do not diffuse. The √t rule therefore rejects moves
that really happened. Of the 166 field moves on the ten market-episode cards (2026-10-15), nine
exceed today's limit:
- eight are VIX, e.g. the April-2025 tariff shock, +30.82 pts within 5 days against a 16.5 limit;
- one is UST_2Y: SVB, −112 bp in 5 days against 94.3.

Live, the 2026-10-14 `taiwan_geopolitical` run lost its VIX leg this way.

This spec adds a per-field **floor** on the horizon factor, so a short horizon never shrinks a
field's limit below a set share of its quarterly cap:
- VIX: floor 1.0;
- UST_2Y, UST_5Y, UST_10Y, UST_30Y and UST: floor 0.5;
- every other field: no floor (behaviour unchanged).

Out of scope: the caps themselves; horizons at or above the reference, where the factor is
already ≥ 1; the 3× ceiling; MOVE (no realized data in the corpus yet); other validation codes.

## Definitions

- **Factor** `s(h)` for horizon `h` and reference `R`: `1.0` when `h` is `None` or ≤ 0 or
  `R` ≤ 0; otherwise `min(√(h / R), 3.0)`. This is today's rule.
- **Floor** `φ(field)`: 1.0 for `VIX`; 0.5 for `UST_2Y`, `UST_5Y`, `UST_10Y`, `UST_30Y`, `UST`;
  0.0 for every other field.
- **Limit** for a field and unit: `cap × max(s(h), φ(field))`, where `cap` is
  `MAGNITUDE_BOUNDS[field][unit]`. It is `None` when there is no cap for that field and unit.

## Acceptance criteria

- **C1**: `synthesis.validate.magnitude_limit(field, unit, *, horizon_days, reference_days=90)
  -> float | None` returns the Limit. Floats to `rel=1e-12`. Oracle: the Definitions by hand,
  with today's caps (VIX 70 pts, UST_2Y 400 bp, UST_10Y 350 bp, IG_OAS 500 bp, USDTWD 50 pct),
  e.g.:
  - VIX at 5 and 10 days: 70;
  - UST_2Y at 5 days: 200;
  - UST_10Y at 10 days: 175;
  - IG_OAS at 10 days: 500·√(10/90) ≈ 166.67 (no floor);
  - VIX at 180 days: 70·√2 (the factor is above the floor);
  - VIX in `bp`: `None`.
- **C2**: `validate_shock_vector(fields, horizon_days=h)` flags `implausible_magnitude` exactly
  when `|value|` exceeds the C1 limit, and no longer flags the realized card moves:
  - **Admitted:** each of the nine moves in Notes, at its horizon, gets no `implausible_magnitude`.
  - **Still flagged:**
    - IG_OAS +440 bp at 10 days (limit 166.67), the DFAST case that motivated `drop_invalid`;
    - VIX +90 pts at 5 days;
    - UST_2Y +450 bp at 10 days;
    - USDTWD +20 pct at 5 days (limit 50·√(5/90) ≈ 11.79).

  Oracle: C1 applied by hand; card moves from the FRED closes in Notes.

## Interface

- `synthesis.validate.magnitude_limit(field: str, unit: str, *, horizon_days: int | None,
  reference_days: int = 90) -> float | None` — new. `_check_magnitude` uses it in the
  implementation.
- `validate_shock_vector` — existing; its magnitude flagging follows C1.

## Notes

- The nine realized moves (largest move within the window, from FRED daily closes; the card
  values for 2026-10-15):

  | episode | field | horizon (trading days) | move | FRED closes |
  |---|---|---|---|---|
  | tariff | VIX | 5 | +30.82 pts | VIXCLS 21.51 (2025-04-02) → 52.33 (2025-04-08) |
  | tariff | VIX | 10 | +30.82 pts | same peak |
  | volmageddon | VIX | 5 | +23.85 pts | VIXCLS 13.47 (2018-02-01) → 37.32 (2018-02-05) |
  | volmageddon | VIX | 10 | +23.85 pts | same peak |
  | covid | VIX | 5 | +23.03 pts | VIXCLS 17.08 (2020-02-21) → 40.11 (2020-02-28) |
  | covid | VIX | 10 | +24.86 pts | to 41.94 (2020-03-06) |
  | yen_carry | VIX | 5 | +22.21 pts | VIXCLS 16.36 (2024-07-31) → 38.57 (2024-08-05) |
  | china_deval | VIX | 10 | +28.51 pts | VIXCLS 12.23 (2015-08-10) → 40.74 (2015-08-24) |
  | svb | UST_2Y | 5 | −112 bp | DGS2 5.05 (2023-03-08) → 3.93 (2023-03-15) |

  Every one fits under the new limits: VIX ≤ 30.82 < 70, and UST_2Y 112 < 200.
- The floors are deliberately generous: they stop the band from rejecting realized jumps, and the
  band still catches gross errors (C2's still-flagged cases). Tighter, per-horizon calibration is
  possible once more episodes exist.
- Fixtures: hand-built `ShockField`s; no network, no `data/`, no clock.
