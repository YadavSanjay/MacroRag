"""C1: check_direction flags fields whose sign contradicts the requested direction."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import check_direction

NAMES = ["USDTWD", "USDJPY", "IG_OAS", "HY_OAS", "VIX", "UST_10Y"]


def _contradicts(direction: str, value: float) -> bool:
    # spec Definitions: up with v < 0, or down with v > 0; v == 0 never contradicts.
    return (direction == "up" and value < 0) or (direction == "down" and value > 0)


@pytest.mark.criterion("C1")
def test_live_case_flags_only_the_contradicted_field(sd):
    # oracle: the Definitions by hand on the 2026-10-14 live values. USDTWD -2.32 with
    # "up" contradicts; USDJPY -4.95 "down", IG_OAS +25 "up", VIX +30.82 "up" agree.
    fields = {n: sd.sf(n, v, u) for n, (v, u) in sd.LIVE.items()}
    diag = SynthesisDiagnostics()
    check_direction(fields, dict(sd.LIVE_DIRECTIONS), diag=diag)
    assert set(fields) == set(sd.LIVE)                       # never drops
    assert fields["USDTWD"].validation == [sd.CODE]
    for n in ("USDJPY", "IG_OAS", "VIX"):
        assert fields[n].validation == [], n
    assert {n: f.value for n, f in fields.items()} == {n: v for n, (v, _) in sd.LIVE.items()}
    assert sd.warns(diag) == [{"code": sd.CODE, "field": "USDTWD", "requested": "up",
                               "value": -2.32}]


@pytest.mark.criterion("C1")
def test_down_contradiction_zero_and_ignored_names(sd):
    # oracle: the Definitions by hand. UST_10Y +40 with "down" contradicts; VIX 0.0 with
    # "up" does not (zero never contradicts); HY_OAS has no direction; "UST_2Y" is
    # requested but not emitted, so it is ignored.
    fields = {"UST_10Y": sd.sf("UST_10Y", 40.0), "VIX": sd.sf("VIX", 0.0, "pts"),
              "HY_OAS": sd.sf("HY_OAS", -15.0)}
    diag = SynthesisDiagnostics()
    check_direction(fields, {"UST_10Y": "down", "VIX": "up", "UST_2Y": "up"}, diag=diag)
    assert fields["UST_10Y"].validation == [sd.CODE]
    assert fields["VIX"].validation == []
    assert fields["HY_OAS"].validation == []
    assert sd.warns(diag) == [{"code": sd.CODE, "field": "UST_10Y", "requested": "down",
                               "value": 40.0}]


@pytest.mark.criterion("C1")
def test_existing_code_not_duplicated_and_no_diag_ok(sd):
    # oracle: C1 "gains direction_mismatch once (not duplicated if already present)";
    # other codes on the field are kept; diag=None is allowed. A second field proves
    # the check ran.
    fields = {"IG_OAS": sd.sf("IG_OAS", -30.0, validation=[sd.CODE, "horizon_unknown"]),
              "HY_OAS": sd.sf("HY_OAS", -90.0)}
    check_direction(fields, {"IG_OAS": "up", "HY_OAS": "up"})
    assert fields["IG_OAS"].validation == [sd.CODE, "horizon_unknown"]
    assert fields["HY_OAS"].validation == [sd.CODE]


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None)
@given(values=st.dictionaries(st.sampled_from(NAMES),
                              st.sampled_from([-55.5, -2.32, -0.01, 0.0, 0.01, 4.95, 120.0]),
                              min_size=1),
       directions=st.dictionaries(st.sampled_from(NAMES + ["UST_2Y", "MOVE"]),
                                  st.sampled_from(["up", "down"])))
def test_property_flags_exactly_the_contradictions(sd, values, directions):
    # oracle: the Definitions computed in the test for every field. A control field
    # (EQUITY_US -5 with "up", always a contradiction) makes a do-nothing check fail.
    fields = {n: sd.sf(n, v) for n, v in values.items()}
    fields["EQUITY_US"] = sd.sf("EQUITY_US", -5.0, "pct")
    dirs = dict(directions, EQUITY_US="up")
    before = copy.deepcopy({n: (f.value, list(f.validation)) for n, f in fields.items()})
    diag = SynthesisDiagnostics()
    check_direction(fields, dirs, diag=diag)
    expected = {n for n, f in fields.items()
                if n in dirs and _contradicts(dirs[n], before[n][0])}
    assert "EQUITY_US" in expected
    assert set(fields) == set(before)
    for n, f in fields.items():
        assert f.value == before[n][0]
        assert f.validation == ([sd.CODE] if n in expected else []), n
    assert sorted((w["field"], w["requested"], w["value"]) for w in sd.warns(diag)) == \
        sorted((n, dirs[n], before[n][0]) for n in expected)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_running_twice_equals_running_once_and_empty_is_inert(sd):
    # oracle: C1. Relation 1: a second run adds no code (once only). Relation 2: None
    # and {} directions leave the fields exactly as they were.
    fields = {"USDTWD": sd.sf("USDTWD", -2.32, "pct"), "VIX": sd.sf("VIX", -3.0, "pts")}
    dirs = {"USDTWD": "up", "VIX": "up"}
    check_direction(fields, dirs)
    once = {n: list(f.validation) for n, f in fields.items()}
    assert once == {"USDTWD": [sd.CODE], "VIX": [sd.CODE]}
    check_direction(fields, dirs)
    assert {n: list(f.validation) for n, f in fields.items()} == once

    for empty in (None, {}):
        clean = {"USDTWD": sd.sf("USDTWD", -2.32, "pct")}
        diag = SynthesisDiagnostics()
        check_direction(clean, empty, diag=diag)
        assert clean["USDTWD"].validation == [] and diag.validation == []
