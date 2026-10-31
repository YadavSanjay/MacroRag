"""C3: check_card_values reads a 1-trading-day card line like a `trading days` line."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import check_card_values

CID = "market_episode_deepseek::000"
# A 1-day card in the spec's chunk-text format (deepseek's Notes values, 2025-01-27).
CARD1 = "\n".join([
    "Market episode: DeepSeek AI selloff. Realized moves over the 1 trading day after the "
    "2025-01-24 close, from FRED daily data.",
    "- VIX (VIXCLS): +3.05 pts after 1 trading day (2025-01-27); "
    "largest move within the window +3.05 pts",
    "- UST_10Y (DGS10): -10.0 bp after 1 trading day (2025-01-27); "
    "largest move within the window -10.0 bp",
    "- USDJPY (DEXJPUS): -0.50 pct after 1 trading day (2025-01-27); "
    "largest move within the window -0.50 pct",
])


@pytest.mark.criterion("C3")
def test_one_day_card_lines_are_checked(ech):
    # oracle: the synth-card-value rules by hand on CARD1. Tolerance = half the last
    # printed decimal + 1e-9: 0.005 for pts/pct, 0.05 for bp.
    # - VIX 1.00 vs peak +3.05: off by 2.05 -> mismatch.
    # - USDJPY -0.506 vs -0.50: off by 0.006 > 0.005 -> mismatch.
    # - UST_10Y -10.04 vs -10.0: off by 0.04 <= 0.05 -> untouched.
    # - UST_2Y cites the card but it has no UST_2Y line -> untouched.
    fields = {"VIX": ech.sf("VIX", 1.00, "pts", [CID]),
              "USDJPY": ech.sf("USDJPY", -0.506, "pct", [CID]),
              "UST_10Y": ech.sf("UST_10Y", -10.04, "bp", [CID]),
              "UST_2Y": ech.sf("UST_2Y", 7.0, "bp", [CID])}
    diag = SynthesisDiagnostics()
    check_card_values(fields, [ech.card_chunk(CID, CARD1)], diag=diag)
    assert fields["VIX"].validation == [ech.CODE]
    assert fields["USDJPY"].validation == [ech.CODE]
    assert fields["UST_10Y"].validation == []
    assert fields["UST_2Y"].validation == []
    got = sorted(ech.warns(diag), key=lambda w: w["field"])
    assert got == [
        {"code": ech.CODE, "field": "USDJPY", "value": -0.506, "card_peaks": {CID: -0.50}},
        {"code": ech.CODE, "field": "VIX", "value": 1.00, "card_peaks": {CID: 3.05}},
    ]
    # rel=1e-12: the value passes through unchanged
    assert fields["VIX"].value == pytest.approx(1.00, rel=1e-12)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@settings(max_examples=60, deadline=None)
@given(peak=st.integers(min_value=-500, max_value=500),
       off=st.integers(min_value=-200, max_value=200),
       k=st.sampled_from([5, 10, 42, 221]))
def test_property_one_day_line_checked_exactly_like_a_days_line(ech, peak, off, k):
    # oracle: the synth-card-value rules computed in the test. A UST_10Y line with
    # largest move peak/10 bp and a field value (peak + off)/10: mismatch iff
    # |off/10| > 0.05 + 1e-9, i.e. off != 0. Relation: the same card written at
    # 1 trading day and at k trading days gives the same annotation. A control field
    # (UST_2Y +8.0 against peak -14.0 on the same line kind) always mismatches.
    def card(h: int) -> str:
        return "\n".join([
            ech.header("X", h, ech.LEHMAN["anchor"]),
            ech.line("UST_10Y (DGS10)", "+0.0", "bp", f"{peak / 10:+.1f}", h, "2008-09-15"),
            ech.line("UST_2Y (DGS2)", "+8.0", "bp", "-14.0", h, "2008-09-15")])

    out = {}
    for h in (1, k):
        fields = {"UST_10Y": ech.sf("UST_10Y", (peak + off) / 10, "bp", [CID]),
                  "UST_2Y": ech.sf("UST_2Y", 8.0, "bp", [CID])}
        check_card_values(fields, [ech.card_chunk(CID, card(h))])
        assert fields["UST_2Y"].validation == [ech.CODE], h
        assert fields["UST_10Y"].validation == ([ech.CODE] if off != 0 else []), h
        out[h] = {n: list(f.validation) for n, f in fields.items()}
    assert out[1] == out[k]
