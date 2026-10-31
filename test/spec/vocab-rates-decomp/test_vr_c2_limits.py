"""C2: plausibility limits for the two new fields."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.validate import magnitude_limit

CAPS = {"REAL_YIELD_10Y": 350.0, "TERM_PREMIUM_10Y": 200.0}   # oracle: spec C2
FLOOR = 0.5


@pytest.mark.criterion("C2")
def test_spec_examples():
    # oracle: spec C2 by hand — at 10 days the floor binds (sqrt(10/90) < 0.5): 175 / 100;
    # at 90 days the factor is 1: 350 / 200. rel=1e-12: closed forms.
    assert magnitude_limit("REAL_YIELD_10Y", "bp", horizon_days=10) == pytest.approx(175.0,
                                                                                    rel=1e-12)
    assert magnitude_limit("TERM_PREMIUM_10Y", "bp", horizon_days=10) == pytest.approx(
        100.0, rel=1e-12)
    assert magnitude_limit("REAL_YIELD_10Y", "bp", horizon_days=90) == pytest.approx(350.0,
                                                                                    rel=1e-12)
    assert magnitude_limit("TERM_PREMIUM_10Y", "bp", horizon_days=90) == pytest.approx(
        200.0, rel=1e-12)


@pytest.mark.criterion("C2")
@settings(max_examples=40, deadline=None)
@given(field=st.sampled_from(sorted(CAPS)), h=st.integers(min_value=1, max_value=2000))
def test_property_limit_is_cap_times_floored_factor(field, h):
    # oracle: the synth-band-floor Definitions computed in the test. rel=1e-12: closed form.
    factor = min(math.sqrt(h / 90), 3.0)
    want = CAPS[field] * max(factor, FLOOR)
    assert magnitude_limit(field, "bp", horizon_days=h) == pytest.approx(want, rel=1e-12)
