"""C1: magnitude_limit is the cap times the horizon factor, floored per field."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.validate import magnitude_limit


@pytest.mark.criterion("C1")
def test_spec_examples(sbf):
    # oracle: the spec's C1 examples by hand. VIX floor 1.0: 70 at 5 and 10 days.
    # UST_2Y floor 0.5: 400 x 0.5 = 200. UST_10Y: 350 x 0.5 = 175. IG_OAS has no
    # floor: 500 x sqrt(10/90). VIX at 180 days: factor sqrt(2) > 1. No cap in bp.
    # rel=1e-12: hand closed forms vs the same float arithmetic.
    assert magnitude_limit("VIX", "pts", horizon_days=5) == pytest.approx(70.0, rel=1e-12)
    assert magnitude_limit("VIX", "pts", horizon_days=10) == pytest.approx(70.0, rel=1e-12)
    assert magnitude_limit("UST_2Y", "bp", horizon_days=5) == pytest.approx(200.0, rel=1e-12)
    assert magnitude_limit("UST_10Y", "bp", horizon_days=10) == pytest.approx(175.0,
                                                                               rel=1e-12)
    assert magnitude_limit("IG_OAS", "bp", horizon_days=10) == pytest.approx(
        500.0 * math.sqrt(10 / 90), rel=1e-12)
    assert magnitude_limit("VIX", "pts", horizon_days=180) == pytest.approx(
        70.0 * math.sqrt(2.0), rel=1e-12)
    assert magnitude_limit("VIX", "bp", horizon_days=10) is None


@pytest.mark.criterion("C1")
def test_no_horizon_and_reference_edge_cases(sbf):
    # oracle: the Definitions — h None or <= 0, or reference <= 0, gives factor 1, so
    # the limit is the cap (every floor is <= 1). The 3x ceiling: VIX at 10000 days
    # with reference 90 is 70 x 3. USDTWD (no floor) at 5 days: 50 x sqrt(5/90).
    # rel=1e-12: hand closed forms vs the same float arithmetic.
    assert magnitude_limit("UST_2Y", "bp", horizon_days=None) == pytest.approx(400.0,
                                                                               rel=1e-12)
    assert magnitude_limit("IG_OAS", "bp", horizon_days=0) == pytest.approx(500.0, rel=1e-12)
    assert magnitude_limit("IG_OAS", "bp", horizon_days=10,
                           reference_days=0) == pytest.approx(500.0, rel=1e-12)
    assert magnitude_limit("VIX", "pts", horizon_days=10000) == pytest.approx(210.0,
                                                                              rel=1e-12)
    assert magnitude_limit("USDTWD", "pct", horizon_days=5) == pytest.approx(
        50.0 * math.sqrt(5 / 90), rel=1e-12)


@pytest.mark.criterion("C1")
@settings(max_examples=80, deadline=None)
@given(field=st.sampled_from(["VIX", "UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "UST",
                              "IG_OAS", "HY_OAS", "USDTWD"]),
       h=st.integers(min_value=1, max_value=2000),
       ref=st.sampled_from([30, 90, 180]))
def test_property_limit_matches_the_definitions(sbf, field, h, ref):
    # oracle: the Definitions computed in the test (conftest.limit).
    # rel=1e-12: the same closed form evaluated twice.
    unit = sbf.CAPS[field][0]
    got = magnitude_limit(field, unit, horizon_days=h, reference_days=ref)
    assert got == pytest.approx(sbf.limit(field, h, ref), rel=1e-12)
