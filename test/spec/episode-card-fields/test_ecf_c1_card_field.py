"""C1: missing_fields reports card_field true for every field an episode card carries."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import missing_fields

# The 19 vocabulary names: spec C1's 11 card fields plus its 8 non-card fields.
_ALL = sorted({"VIX", "UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "IG_OAS", "HY_OAS",
               "USDTWD", "USDJPY", "REAL_YIELD_10Y", "TERM_PREMIUM_10Y", "UST", "MOVE",
               "MOVE_1Y", "MOVE_5Y", "MOVE_10Y", "MOVE_30Y", "EQUITY_US", "XCCY_BASIS"})
_NEW = ("REAL_YIELD_10Y", "TERM_PREMIUM_10Y")


@pytest.mark.criterion("C1")
def test_new_fields_not_emitted_and_dropped():
    # oracle: spec C1 by hand. REAL_YIELD_10Y never emitted, TERM_PREMIUM_10Y dropped with
    # codes [horizon_mismatch]; both card fields per the Requirement (episodes.CANONICAL
    # maps DFII10 and ACMTP10 to them). UST_10Y kept, so absent; MOVE never emitted, false.
    dropped = [{"field": "TERM_PREMIUM_10Y", "stage": "validate",
                "reason": "invalid_magnitude", "codes": ["horizon_mismatch"]}]
    got = missing_fields(["REAL_YIELD_10Y", "UST_10Y", "TERM_PREMIUM_10Y", "MOVE"],
                         ["UST_10Y"], dropped)
    assert got == [
        {"field": "REAL_YIELD_10Y", "reason": "not_emitted", "codes": [],
         "card_field": True},
        {"field": "TERM_PREMIUM_10Y", "reason": "dropped", "codes": ["horizon_mismatch"],
         "card_field": True},
        {"field": "MOVE", "reason": "not_emitted", "codes": [], "card_field": False},
    ]


@pytest.mark.criterion("C1")
def test_whole_vocabulary_lower_case(ecf):
    # oracle: spec C1 lists, copied into conftest (11 card fields true, 8 non-card false).
    # Every name requested lower-case, none kept; drop records (reason fallback) on two.
    req = [n.lower() for n in _ALL]
    dropped = [{"field": "REAL_YIELD_10Y", "stage": "gate", "reason": "no_dated_citation"},
               {"field": "EQUITY_US", "stage": "gate", "reason": "no_dated_citation"}]
    got = missing_fields(req, [], dropped)
    expected = []
    for n in _ALL:
        if n in ("REAL_YIELD_10Y", "EQUITY_US"):
            expected.append(ecf.entry(n, "dropped", ["no_dated_citation"]))
        else:
            expected.append(ecf.entry(n))
    assert got == expected
    assert {e["field"] for e in got if e["card_field"]} == ecf.CARD_FIELDS
    assert {e["field"] for e in got if not e["card_field"]} == ecf.NON_CARD_FIELDS


@pytest.mark.criterion("C1")
@settings(max_examples=80, deadline=None)
@given(order=st.permutations(_ALL),
       upper=st.lists(st.booleans(), min_size=len(_ALL), max_size=len(_ALL)),
       kept=st.sets(st.sampled_from([n for n in _ALL if n not in _NEW])),
       drops=st.lists(st.tuples(st.sampled_from(_ALL), st.sampled_from(["r1", "r2"]),
                                st.one_of(st.none(),
                                          st.lists(st.sampled_from(["c1", "c2"]),
                                                   max_size=2))),
                      max_size=6))
def test_property_card_field_matches_spec_set(ecf, order, upper, kept, drops):
    # oracle: the C1 rules computed in the test, card_field from the spec's 11-name literal.
    # REAL_YIELD_10Y and TERM_PREMIUM_10Y are never kept, so every example checks both.
    req = [n if u else n.lower() for n, u in zip(order, upper, strict=True)]
    dropped = []
    for f, reason, codes in drops:
        rec = {"field": f, "stage": "s", "reason": reason}
        if codes is not None:
            rec["codes"] = codes
        dropped.append(rec)
    expected = []
    for name in order:
        if name in kept:
            continue
        recs = [d for d in dropped if d["field"] == name]
        codes: list[str] = []
        for d in recs:
            for c in (d["codes"] if "codes" in d else [d["reason"]]):
                if c not in codes:
                    codes.append(c)
        expected.append(ecf.entry(name, "dropped" if recs else "not_emitted", codes))
    assert missing_fields(req, sorted(kept), dropped) == expected
