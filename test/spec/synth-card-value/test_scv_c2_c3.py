"""C2: card_value_mismatch is hard. C3: the pipeline applies it before drop_invalid."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import HARD_VALIDATION_CODES, validate_shock_vector


@pytest.mark.criterion("C2")
def test_code_is_hard_and_dropped(scv):
    # oracle: the existing hard-code rule — drop_invalid removes a field carrying a hard
    # code and records it; the membership assertion keeps this red until the code is hard.
    assert scv.CODE in HARD_VALIDATION_CODES
    fields = {"UST_2Y": scv.sf("UST_2Y", 8.0, "bp", [scv.CARD_CID], validation=[scv.CODE]),
              "UST_10Y": scv.sf("UST_10Y", 23.0, "bp", [scv.CARD_CID])}
    diag = SynthesisDiagnostics()
    out = validate_shock_vector(fields, drop_invalid=True, diag=diag)
    assert set(out) == {"UST_10Y"}
    assert [d["field"] for d in diag.dropped] == ["UST_2Y"]


@pytest.mark.criterion("C2")
@settings(max_examples=20, deadline=None)
@given(flagged=st.dictionaries(st.sampled_from(["UST_2Y", "UST_10Y", "IG_OAS", "VIX"]),
                               st.booleans(), min_size=1))
def test_property_drop_set_is_flagged_set(scv, flagged):
    # oracle: C2 by hand — with drop_invalid exactly the flagged fields are removed.
    units = {"VIX": "pts"}
    fields = {n: scv.sf(n, 1.0, units.get(n, "bp"), [scv.CARD_CID],
                        validation=[scv.CODE] if f else [])
              for n, f in flagged.items()}
    out = validate_shock_vector(fields, drop_invalid=True)
    assert set(out) == {n for n, f in flagged.items() if not f}


def _vector(scv, two_y: float) -> dict:
    return {n: {"value": v, "unit": "bp", "chunk_ids": [scv.CARD_CID],
                "grounding_mode": "analog", "rationale": "card"}
            for n, v in (("UST_2Y", two_y), ("UST_10Y", 23.0), ("IG_OAS", 16.0))}


@pytest.mark.criterion("C3")
def test_pipeline_drops_the_end_value_field(scv, tmp_path):
    # oracle: C1 + C2 by hand on the live case: UST_2Y +8.0 (end) vs largest -14.0 ->
    # dropped and reported in missing_fields; UST_10Y and IG_OAS carry the peaks -> kept.
    body = scv.run_pipeline(tmp_path, _vector(scv, 8.0),
                            fields=["UST_2Y", "UST_10Y", "IG_OAS"])
    assert set(body["shock_vector"]) == {"UST_10Y", "IG_OAS"}
    missing = body["synthesis_diagnostics"]["missing_fields"]
    assert [(m["field"], m["reason"]) for m in missing] == [("UST_2Y", "dropped")]
    assert scv.CODE in missing[0]["codes"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_peak_value_kept_end_value_dropped(scv):
    # oracle: C3. Relation: the same request with UST_2Y at the card's largest move
    # (-14.0) keeps it; at the end value (+8.0) drops it.
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        peak = scv.run_pipeline(a, _vector(scv, -14.0))
        end = scv.run_pipeline(b, _vector(scv, 8.0))
    assert "UST_2Y" in peak["shock_vector"]
    assert "UST_2Y" not in end["shock_vector"]
