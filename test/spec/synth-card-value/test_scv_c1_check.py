"""C1: check_card_values flags fields that do not carry the cited card's largest move."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import check_card_values


@pytest.mark.criterion("C1")
def test_live_case_end_value_flagged_largest_kept(scv):
    # oracle: the Definitions by hand on the refunding card. UST_2Y +8.0 is the end value;
    # the largest move is -14.0 -> mismatch. UST_10Y +23.0 = peak, IG_OAS +16.0 = the
    # stand-in line's peak, VIX +3.47 = peak (pts tolerance 0.005) -> no code.
    # UST_5Y cites the card but the card has no UST_5Y line -> untouched.
    fields = {"UST_2Y": scv.sf("UST_2Y", 8.0, "bp", [scv.CARD_CID, scv.NOTE_CID]),
              "UST_10Y": scv.sf("UST_10Y", 23.0, "bp", [scv.CARD_CID]),
              "IG_OAS": scv.sf("IG_OAS", 16.0, "bp", [scv.CARD_CID]),
              "VIX": scv.sf("VIX", 3.47, "pts", [scv.CARD_CID]),
              "UST_5Y": scv.sf("UST_5Y", 99.0, "bp", [scv.CARD_CID])}
    diag = SynthesisDiagnostics()
    check_card_values(fields, [scv.card_chunk(), scv.note_chunk()], diag=diag)
    assert fields["UST_2Y"].validation == [scv.CODE]
    for n in ("UST_10Y", "IG_OAS", "VIX", "UST_5Y"):
        assert fields[n].validation == [], n
    assert scv.warns(diag) == [{"code": scv.CODE, "field": "UST_2Y", "value": 8.0,
                                "card_peaks": {scv.CARD_CID: -14.0}}]
    # rel=1e-12: the value passes through unchanged
    assert fields["UST_2Y"].value == pytest.approx(8.0, rel=1e-12)


@pytest.mark.criterion("C1")
def test_tolerance_prose_only_label_lines_and_any_card_rule(scv):
    # oracle: the Definitions by hand.
    # - UST_10Y +23.04 is within 0.05 of +23.0 -> ok; +23.06 is not -> mismatch.
    # - A field citing only prose is untouched even with a card-like number.
    # - THREEFYTP10 is not a shock field, so its line constrains nothing.
    # - Two cited cards: matching either card's peak is enough.
    other = scv.card_chunk(scv.CARD_TEXT.replace("-14.0 bp", "+8.0 bp"),
                           cid="market_episode_other::001")
    fields = {"UST_10Y": scv.sf("UST_10Y", 23.04, "bp", [scv.CARD_CID])}
    check_card_values(fields, [scv.card_chunk()])
    assert fields["UST_10Y"].validation == []
    fields = {"UST_10Y": scv.sf("UST_10Y", 23.06, "bp", [scv.CARD_CID]),
              "UST_30Y": scv.sf("UST_30Y", 8.0, "bp", [scv.NOTE_CID]),
              "UST_2Y": scv.sf("UST_2Y", 8.0, "bp", [scv.CARD_CID, other.chunk_id])}
    check_card_values(fields, [scv.card_chunk(), scv.note_chunk(), other])
    assert fields["UST_10Y"].validation == [scv.CODE]
    assert fields["UST_30Y"].validation == []
    assert fields["UST_2Y"].validation == []


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None)
@given(peak=st.integers(min_value=-500, max_value=500), off=st.integers(min_value=-200,
                                                                        max_value=200))
def test_property_flag_iff_outside_tolerance(scv, peak, off):
    # oracle: the Definitions computed in the test. A card line with largest move
    # peak/10 bp (one decimal) and a field value (peak + off)/10 bp: mismatch iff
    # |off/10| > 0.05 + 1e-9, i.e. off != 0. A control field (UST_2Y +8.0, peak -14.0)
    # always mismatches, so a do-nothing check fails.
    text = "\n".join(["Market episode: X.",
                      scv.line("UST_10Y (DGS10)", "+0.0", "bp", f"{peak / 10:+.1f}"),
                      scv.line("UST_2Y (DGS2)", "+8.0", "bp", "-14.0")])
    card = scv.card_chunk(text)
    fields = {"UST_10Y": scv.sf("UST_10Y", (peak + off) / 10, "bp", [scv.CARD_CID]),
              "UST_2Y": scv.sf("UST_2Y", 8.0, "bp", [scv.CARD_CID])}
    check_card_values(fields, [card])
    assert fields["UST_2Y"].validation == [scv.CODE]
    assert fields["UST_10Y"].validation == ([scv.CODE] if off != 0 else [])
