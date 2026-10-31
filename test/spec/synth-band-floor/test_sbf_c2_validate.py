"""C2: validate_shock_vector admits realized card moves and still flags gross errors."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# spec Notes: the nine realized moves (field, unit, value, horizon in days), from FRED
# closes, e.g. tariff VIXCLS 21.51 -> 52.33 = +30.82; SVB DGS2 5.05 -> 3.93 = -112 bp.
REALIZED = [
    ("tariff 5d", "VIX", "pts", 52.33 - 21.51, 5),
    ("tariff 10d", "VIX", "pts", 52.33 - 21.51, 10),
    ("volmageddon 5d", "VIX", "pts", 37.32 - 13.47, 5),
    ("volmageddon 10d", "VIX", "pts", 37.32 - 13.47, 10),
    ("covid 5d", "VIX", "pts", 40.11 - 17.08, 5),
    ("covid 10d", "VIX", "pts", 41.94 - 17.08, 10),
    ("yen_carry 5d", "VIX", "pts", 38.57 - 16.36, 5),
    ("china_deval 10d", "VIX", "pts", 40.74 - 12.23, 10),
    ("svb 5d", "UST_2Y", "bp", (3.93 - 5.05) * 100, 5),
]


@pytest.mark.criterion("C2")
def test_realized_card_moves_are_admitted(sbf):
    # oracle: C1 by hand — every realized move is under its new limit (VIX 70; UST_2Y
    # 200), while today each one exceeds its sqrt-scaled limit.
    for label, field, unit, value, h in REALIZED:
        assert not sbf.flagged(field, value, unit, h), label


@pytest.mark.criterion("C2")
def test_gross_errors_still_flagged_and_realized_admitted(sbf):
    # oracle: C1 by hand. IG_OAS +440 > 500 x sqrt(10/90) = 166.67; VIX +90 > 70;
    # UST_2Y +450 > 200; USDTWD +20 > 50 x sqrt(5/90) = 11.79. In the same call the
    # tariff VIX move (+30.82 < 70) is admitted.
    assert sbf.flagged("IG_OAS", 440.0, "bp", 10)
    assert sbf.flagged("VIX", 90.0, "pts", 5)
    assert sbf.flagged("UST_2Y", 450.0, "bp", 10)
    assert sbf.flagged("USDTWD", 20.0, "pct", 5)
    assert not sbf.flagged("VIX", 30.82, "pts", 5)


@pytest.mark.criterion("C2")
@settings(max_examples=80, deadline=None)
@given(field=st.sampled_from(["VIX", "UST_2Y", "UST_10Y", "IG_OAS", "USDTWD"]),
       h=st.integers(min_value=1, max_value=60),
       frac=st.floats(min_value=0.05, max_value=2.0, allow_nan=False))
def test_property_flag_iff_over_the_limit(sbf, field, h, frac):
    # oracle: the Definitions computed in the test: flagged exactly when |value| exceeds
    # cap x max(factor, floor). Values stay off the boundary (frac never in
    # [0.999, 1.001]) so float rounding cannot decide the case.
    if 0.999 <= frac <= 1.001:
        frac = 0.9
    unit = sbf.CAPS[field][0]
    lim = sbf.limit(field, h)
    for sign in (1.0, -1.0):
        assert sbf.flagged(field, sign * frac * lim, unit, h) == (frac > 1.0), \
            (field, h, frac, lim)
