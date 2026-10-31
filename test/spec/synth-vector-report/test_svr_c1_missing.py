"""C1: missing_fields says why each requested field is absent."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import missing_fields

CARD = {"VIX", "UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "IG_OAS", "HY_OAS", "USDTWD",
        "USDJPY"}


@pytest.mark.criterion("C1")
def test_rate_capital_shape():
    # oracle: the C1 rules by hand on faultline's rate_capital request. Requested
    # UST_2Y, UST_5Y, UST_10Y, UST_30Y, MOVE, IG_OAS; kept UST_10Y, UST_30Y, IG_OAS;
    # MOVE dropped with codes [horizon_mismatch]; UST_2Y/5Y never emitted (card fields).
    dropped = [{"field": "MOVE", "stage": "validate", "reason": "invalid_magnitude",
                "codes": ["horizon_mismatch"]}]
    got = missing_fields(["UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "MOVE", "IG_OAS"],
                         ["UST_10Y", "UST_30Y", "IG_OAS"], dropped)
    assert got == [
        {"field": "UST_2Y", "reason": "not_emitted", "codes": [], "card_field": True},
        {"field": "UST_5Y", "reason": "not_emitted", "codes": [], "card_field": True},
        {"field": "MOVE", "reason": "dropped", "codes": ["horizon_mismatch"],
         "card_field": False},
    ]


@pytest.mark.criterion("C1")
def test_case_duplicates_reason_fallback_and_empty():
    # oracle: the C1 rules by hand. "vix" is upper-cased and listed once although
    # requested twice; its two drop records give codes from "codes" when present, else
    # "reason", de-duplicated in record order; a kept dict counts by its keys. None and
    # [] give [].
    dropped = [{"field": "VIX", "stage": "synthesize", "reason": "invalid_unit"},
               {"field": "VIX", "stage": "validate", "reason": "invalid_magnitude",
                "codes": ["implausible_magnitude", "invalid_unit"]},
               {"field": "HY_OAS", "stage": "gate", "reason": "no_dated_citation"}]
    got = missing_fields(["vix", "IG_OAS", "VIX", "MOVE_5Y"], {"IG_OAS": object()}, dropped)
    assert got == [
        {"field": "VIX", "reason": "dropped",
         "codes": ["invalid_unit", "implausible_magnitude"], "card_field": True},
        {"field": "MOVE_5Y", "reason": "not_emitted", "codes": [], "card_field": False},
    ]
    assert missing_fields(None, [], dropped) == []
    assert missing_fields([], ["VIX"], dropped) == []


@pytest.mark.criterion("C1")
@settings(max_examples=80, deadline=None)
@given(requested=st.lists(st.sampled_from(["VIX", "ust_2y", "UST_10Y", "MOVE", "IG_OAS",
                                           "USDTWD", "EQUITY_US"]), min_size=1),
       kept=st.sets(st.sampled_from(["VIX", "UST_2Y", "UST_10Y", "MOVE", "IG_OAS"])),
       drops=st.lists(st.tuples(st.sampled_from(["VIX", "UST_2Y", "MOVE", "USDTWD"]),
                                st.sampled_from(["r1", "r2"]),
                                st.one_of(st.none(), st.lists(st.sampled_from(["c1", "c2"]),
                                                              max_size=2)))))
def test_property_matches_the_rules(requested, kept, drops):
    # oracle: the C1 rules computed in the test. A control request (EQUITY_US, never
    # kept or dropped) guarantees at least one entry, so a do-nothing function fails.
    req = list(requested) + ["EQUITY_US"]
    dropped = []
    for f, reason, codes in drops:
        rec = {"field": f, "stage": "s", "reason": reason}
        if codes is not None:
            rec["codes"] = codes
        dropped.append(rec)
    expected, seen = [], set()
    for name in (r.upper() for r in req):
        if name in seen or name in kept:
            continue
        seen.add(name)
        recs = [d for d in dropped if d["field"] == name]
        codes: list[str] = []
        for d in recs:
            for c in (d["codes"] if "codes" in d else [d["reason"]]):
                if c not in codes:
                    codes.append(c)
        expected.append({"field": name, "reason": "dropped" if recs else "not_emitted",
                         "codes": codes, "card_field": name in CARD})
    assert missing_fields(req, sorted(kept), dropped) == expected
