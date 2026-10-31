"""C2: synthesize_shock_vector parses the model's per-field source_horizon_days."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llm.fake import FakeLLM
from synthesis.schema import ShockField
from synthesis.synthesizer import synthesize_shock_vector

CID = "note::000"
CTRL = "HY_OAS"   # always declares 30 (a valid int): fails a do-nothing parser
ABSENT = object()


def _spec(raw):
    out = {"value": 25, "unit": "bp", "chunk_ids": [CID], "grounding_mode": "direct",
           "rationale": "evidence"}
    if raw is not ABSENT:
        out["source_horizon_days"] = raw
    return out


def _parse(sah, raws: dict, units: dict | None = None):
    """Run the synthesizer over a scripted model emitting ``raws`` (field -> raw)."""
    vector = {name: _spec(raw) for name, raw in raws.items()}
    for name, unit in (units or {}).items():
        vector[name]["unit"] = unit
    vector[CTRL] = _spec(30)
    llm = FakeLLM(responder=lambda system, user: json.dumps({"shock_vector": vector}))
    out = synthesize_shock_vector(llm, "q", [sah.chunk(CID, doc_type="research_note")],
                                  horizon_days=10)
    assert set(out) == set(vector)          # every field is kept
    assert out[CTRL].source_horizon_days == 30
    assert type(out[CTRL].source_horizon_days) is int
    return out


def _assert_stored(f, expected):
    if expected is None:
        assert f.source_horizon_days is None
    else:
        assert f.source_horizon_days == expected
        assert type(f.source_horizon_days) is int


@pytest.mark.criterion("C2")
def test_listed_forms_map_by_hand(sah):
    # oracle: C2's list mapped by hand. 10 -> 10; "90" (numeric string) -> 90;
    # 30.0 (whole-valued float) -> 30. Absent, None, 0, -5, "abc", 7.5, True,
    # False, "0", "-3", "7.5", [30] -> None, field kept.
    raws = {"UST_2Y": 10, "UST_5Y": "90", "UST_10Y": 30.0,
            "UST_30Y": ABSENT, "UST": None, "IG_OAS": 0, "VIX": -5}
    expected = {"UST_2Y": 10, "UST_5Y": 90, "UST_10Y": 30, "UST_30Y": None,
                "UST": None, "IG_OAS": None, "VIX": None}
    # VIX is pts-denominated: give it a valid unit so it survives parsing.
    out = _parse(sah, raws, {"VIX": "pts"})
    for name, exp in expected.items():
        _assert_stored(out[name], exp)

    raws = {"UST_2Y": "abc", "UST_5Y": 7.5, "UST_10Y": True, "UST_30Y": False,
            "UST": "0", "IG_OAS": "-3", "XCCY_BASIS": "7.5"}
    out = _parse(sah, raws)
    for name in raws:
        _assert_stored(out[name], None)
    raws = {"UST_2Y": [30], "UST_5Y": {"days": 30}}
    out = _parse(sah, raws)
    for name in raws:
        _assert_stored(out[name], None)


@pytest.mark.criterion("C2")
def test_to_dict_emits_key_only_when_set(sah):
    # oracle: C2 "to_dict() includes source_horizon_days exactly when it isn't None".
    with_h = ShockField("UST_10Y", 15.0, "bp", chunk_ids=[CID], source_horizon_days=90)
    without = ShockField("UST_10Y", 15.0, "bp", chunk_ids=[CID])
    assert with_h.to_dict().get("source_horizon_days") == 90
    assert "source_horizon_days" not in without.to_dict()
    # the rest of the payload is unchanged by the new key
    d = with_h.to_dict()
    d.pop("source_horizon_days", None)
    assert d == without.to_dict()


@pytest.mark.criterion("C2")
@settings(max_examples=60)
@given(raw=st.one_of(
    st.integers(-50, 400),
    st.integers(-50, 400).map(str),
    st.integers(-50, 400).map(float),
    st.integers(-50, 400).map(lambda n: n + 0.5),
    st.booleans(),
    st.none(),
    st.sampled_from(["", "abc", "ten", "1/2"]),
))
def test_parse_property(sah, raw):
    # oracle: C2's rule written out here: an int > 0 (not bool), a numeric string of
    # an int > 0, or a whole-valued float equal to an int > 0 -> that int; else None.
    if isinstance(raw, bool) or raw is None:
        expected = None
    elif isinstance(raw, int):
        expected = raw if raw > 0 else None
    elif isinstance(raw, float):
        expected = int(raw) if raw.is_integer() and raw > 0 else None
    else:
        expected = int(raw) if raw.lstrip("-").isdigit() and int(raw) > 0 else None
    out = _parse(sah, {"UST_10Y": raw})
    _assert_stored(out["UST_10Y"], expected)
    # tolerance: 25 round-trips through JSON and float(); exact up to 1e-12.
    assert out["UST_10Y"].value == pytest.approx(25.0, abs=1e-12)
    assert ("source_horizon_days" in out["UST_10Y"].to_dict()) is (expected is not None)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_declaration_does_not_change_other_attributes(sah):
    # oracle: C2 "the field itself is kept": adding or varying source_horizon_days
    # changes only that attribute, never value, unit, citations or mode.
    base = _parse(sah, {"UST_10Y": ABSENT})["UST_10Y"]
    for raw, exp in [(45, 45), ("abc", None), (0, None)]:
        f = _parse(sah, {"UST_10Y": raw})["UST_10Y"]
        assert (f.name, f.value, f.unit, f.chunk_ids, f.grounding_mode, f.rationale) == (
            base.name, base.value, base.unit, base.chunk_ids, base.grounding_mode,
            base.rationale)
        _assert_stored(f, exp)
