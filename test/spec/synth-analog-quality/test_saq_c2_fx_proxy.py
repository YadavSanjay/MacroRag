"""C2: fx_proxy_unlisted flags an analog FX field anchored on a non-allowlisted currency."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import check_analog_quality

# The spec's C2 data, copied verbatim.
ALLOW = {
    "USDTWD": {"TWD", "KRW", "CNY", "CNH", "SGD", "HKD", "MYR", "THB", "PHP", "IDR", "INR"},
    "USDJPY": {"JPY", "CHF"},
}
RECOGNISED = sorted(ALLOW["USDTWD"] | ALLOW["USDJPY"] | {
    "USD", "EUR", "GBP", "AUD", "NZD", "CAD", "NOK", "SEK", "MXN", "BRL", "ZAR", "TRY",
    "RUB", "UAH", "PLN", "HUF", "CZK", "ILS"})

FX_CID = "fx_note::000"
FX_TEXT = "the currency moved sharply during the episode"   # no cited quantity (C1 inert)


def _field(saq, name, rationale, mode="analog"):
    return saq.sf(name, 5.0, "pct", [FX_CID], rationale=rationale, mode=mode)


def _run(saq, fields):
    diag = SynthesisDiagnostics()
    check_analog_quality(fields, [saq.chunk(FX_CID, FX_TEXT)], diag=diag)
    return diag


def _check(saq, diag, f, named):
    """``named`` is the expected set of non-USD codes when flagged, else None."""
    w = saq.warns(diag, saq.CODE_FX, f.name)
    if named is None:
        assert saq.CODE_FX not in f.validation
        assert w == []
        return
    assert set(f.validation) == {saq.CODE_FX}
    assert len(w) == 1
    assert w[0]["named"] == sorted(w[0]["named"])
    # USD may or may not be listed in `named` (spec: "other than USD" is the trigger).
    assert set(w[0]["named"]) - {"USD"} == named


@pytest.mark.criterion("C2")
def test_spec_oracle_rationales(saq):
    # oracle: the spec's C2 oracle list, allowlist applied by hand.
    # USDTWD analog "USD/UAH rose 5 percent": named {USD, UAH}; non-USD UAH, not in the
    #   USDTWD list -> flag. "KRW and TWD weakened": both listed -> no flag.
    #   "the currency fell": no recognised code -> no flag.
    cases = [("USD/UAH rose 5 percent", {"UAH"}),
             ("KRW and TWD weakened", None),
             ("the currency fell", None)]
    for rationale, named in cases:
        f = _field(saq, "USDTWD", rationale)
        _check(saq, _run(saq, {"USDTWD": f}), f, named)


@pytest.mark.criterion("C2")
def test_scope_and_matching_rules(saq):
    # oracle: C2 rules by hand.
    # USDJPY analog "EUR and GBP fell": EUR, GBP not in {JPY, CHF} -> flag, named EUR, GBP.
    # USDJPY analog "CHF rallied as EUR fell": CHF is listed -> no flag.
    # USDTWD analog "UAH and KRW fell": KRW is listed -> no flag.
    # USDTWD analog "the uah fell": lower case is not a code -> no flag.
    # USDTWD analog "USD rallied": only USD named -> no flag.
    # USDTWD direct "USD/UAH rose": direct field -> never flagged.
    # IG_OAS analog "UAH spreads": not an allowlisted field -> never flagged.
    f = _field(saq, "USDJPY", "EUR and GBP fell")
    _check(saq, _run(saq, {"USDJPY": f}), f, {"EUR", "GBP"})
    for name, rationale, mode in [("USDJPY", "CHF rallied as EUR fell", "analog"),
                                  ("USDTWD", "UAH and KRW fell", "analog"),
                                  ("USDTWD", "the uah fell", "analog"),
                                  ("USDTWD", "USD rallied", "analog"),
                                  ("USDTWD", "USD/UAH rose", "direct")]:
        f = _field(saq, name, rationale, mode)
        ctrl = _field(saq, "USDJPY" if name == "USDTWD" else "USDTWD", "UAH fell")
        diag = _run(saq, {name: f, ctrl.name: ctrl})
        _check(saq, diag, f, None)
        _check(saq, diag, ctrl, {"UAH"})
    f = saq.sf("IG_OAS", 40.0, "bp", [FX_CID], rationale="UAH spreads", mode="analog")
    ctrl = _field(saq, "USDTWD", "UAH fell")
    diag = _run(saq, {"IG_OAS": f, "USDTWD": ctrl})
    _check(saq, diag, f, None)
    _check(saq, diag, ctrl, {"UAH"})


@pytest.mark.criterion("C2")
@settings(max_examples=30)
@given(name=st.sampled_from(sorted(ALLOW)), analog=st.booleans(),
       codes=st.lists(st.sampled_from(RECOGNISED), max_size=3, unique=True),
       pair=st.booleans(), noise=st.sampled_from(["", "uah", "eur", "twd"]))
def test_flag_iff_rule(saq, name, analog, codes, pair, noise):
    # oracle: the C2 rule computed in the test from the spec's allowlist/recognised sets.
    words = [f"USD/{c}" if pair and c != "USD" else c for c in codes]
    rationale = "the " + " and ".join(words + ([noise] if noise else [])) + " moved"
    non_usd = set(codes) - {"USD"}
    flagged = analog and bool(non_usd) and not (set(codes) & ALLOW[name])
    f = _field(saq, name, rationale, "analog" if analog else "direct")
    ctrl_name = "USDJPY" if name == "USDTWD" else "USDTWD"
    ctrl = _field(saq, ctrl_name, "UAH fell")   # always flags: guards a do-nothing body
    diag = _run(saq, {name: f, ctrl_name: ctrl})
    _check(saq, diag, f, non_usd if flagged else None)
    _check(saq, diag, ctrl, {"UAH"})
