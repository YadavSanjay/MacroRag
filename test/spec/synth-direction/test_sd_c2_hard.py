"""C2: direction_mismatch is a hard code, dropped when drop_invalid is on."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import HARD_VALIDATION_CODES, validate_shock_vector


@pytest.mark.criterion("C2")
def test_code_is_hard_and_dropped_with_drop_invalid(sd):
    # oracle: the existing hard-code rule (validate_shock_vector drops a field whose
    # validation intersects HARD_VALIDATION_CODES and records the drop with its codes).
    # Values are small so no magnitude code applies; no coherence pair is present.
    assert sd.CODE in HARD_VALIDATION_CODES
    fields = {"USDTWD": sd.sf("USDTWD", -2.32, "pct", validation=[sd.CODE]),
              "VIX": sd.sf("VIX", 4.0, "pts")}
    diag = SynthesisDiagnostics()
    out = validate_shock_vector(fields, drop_invalid=True, diag=diag)
    assert set(out) == {"VIX"}
    drops = [d for d in diag.dropped if d["field"] == "USDTWD"]
    assert len(drops) == 1 and sd.CODE in drops[0]["codes"]


@pytest.mark.criterion("C2")
def test_kept_and_annotated_without_drop_invalid(sd):
    # oracle: C2 — with drop_invalid False a hard-coded field stays, annotated; the
    # membership assertion keeps this red until the code is hard.
    assert sd.CODE in HARD_VALIDATION_CODES
    fields = {"USDTWD": sd.sf("USDTWD", -2.32, "pct", validation=[sd.CODE])}
    diag = SynthesisDiagnostics()
    out = validate_shock_vector(fields, drop_invalid=False, diag=diag)
    assert set(out) == {"USDTWD"}
    assert out["USDTWD"].validation == [sd.CODE]
    assert diag.dropped == []


@pytest.mark.criterion("C2")
@settings(max_examples=40, deadline=None)
@given(flagged=st.dictionaries(
    st.sampled_from(["USDTWD", "USDJPY", "IG_OAS", "UST_10Y", "UST_2Y"]), st.booleans(),
    min_size=1))
def test_property_drop_set_is_the_flagged_set(sd, flagged):
    # oracle: C2 by hand — with drop_invalid, exactly the fields carrying
    # direction_mismatch are removed (small values: no other hard code applies).
    fields = {n: sd.sf(n, 1.0, "pct" if n.startswith("USD") else "bp",
                       validation=[sd.CODE] if f else [])
              for n, f in flagged.items()}
    diag = SynthesisDiagnostics()
    out = validate_shock_vector(fields, drop_invalid=True, diag=diag)
    assert set(out) == {n for n, f in flagged.items() if not f}
    assert {d["field"] for d in diag.dropped} == {n for n, f in flagged.items() if f}
