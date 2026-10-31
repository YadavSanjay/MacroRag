"""C2: the committed endpoint builds the sell-off cards from FRED rows."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

TP_LABEL = "THREEFYTP10 (Kim-Wright 10-year term premium)"
TAGS = ["forward_guidance", "term_premium", "rates_vol", "discount_rate", "cross_asset_scenario"]


def _card(rs, rows_by_series):
    with tempfile.TemporaryDirectory() as root:
        rs.write_series(Path(root), rows_by_series)
        return rs.committed_card(Path(root))


@pytest.mark.criterion("C2")
def test_taper_card_from_the_notes_rows(rs):
    # oracle: the market-episodes Definitions applied by hand to the spec's Notes rows.
    # Anchor 2013-06-18; days 1..5 end 2013-06-25, days 1..10 end 2013-07-02.
    #   VIXCLS v0 16.61: +0.03 +3.88 +2.29 +3.50 +1.86 | +0.60 +0.25 +0.25 -0.24 -0.17
    #   DGS2 v0 0.27 (bp): +4 +6 +11 +15 +16 | +12 +9 +9 +7 +7
    #   DGS10 v0 2.20 (bp): +13 +21 +32 +37 +40 | +35 +29 +32 +30 +28
    #   THREEFYTP10 v0 0.13 (bp): +6 +11 +17 +21 +23 | +22 +19 +20 +19 +19
    doc = _card(rs, rs.notes_rows())
    d5, d10 = "2013-06-25", "2013-07-02"
    rs.assert_moves(doc.fields["moves"]["5"], {
        "VIXCLS": rs.move("VIX", "pts", 1.86, 3.88, d5),
        "DGS2": rs.move("UST_2Y", "bp", 16.0, 16.0, d5),
        "DGS10": rs.move("UST_10Y", "bp", 40.0, 40.0, d5),
        "THREEFYTP10": rs.move(None, "bp", 23.0, 23.0, d5)})
    rs.assert_moves(doc.fields["moves"]["10"], {
        "VIXCLS": rs.move("VIX", "pts", -0.17, 3.88, d10),
        "DGS2": rs.move("UST_2Y", "bp", 7.0, 16.0, d10),
        "DGS10": rs.move("UST_10Y", "bp", 28.0, 40.0, d10),
        "THREEFYTP10": rs.move(None, "bp", 19.0, 23.0, d10)})
    hdr = ("Market episode: Taper tantrum. Realized moves over the {k} trading days after the "
           "2013-06-18 close, from FRED daily data.")
    text5 = "\n".join([
        hdr.format(k=5),
        "- VIX (VIXCLS): +1.86 pts after 5 trading days (2013-06-25); "
        "largest move within the window +3.88 pts",
        "- UST_2Y (DGS2): +16.0 bp after 5 trading days (2013-06-25); "
        "largest move within the window +16.0 bp",
        "- UST_10Y (DGS10): +40.0 bp after 5 trading days (2013-06-25); "
        "largest move within the window +40.0 bp",
        f"- {TP_LABEL}: +23.0 bp after 5 trading days (2013-06-25); "
        "largest move within the window +23.0 bp"])
    text10 = "\n".join([
        hdr.format(k=10),
        "- VIX (VIXCLS): -0.17 pts after 10 trading days (2013-07-02); "
        "largest move within the window +3.88 pts",
        "- UST_2Y (DGS2): +7.0 bp after 10 trading days (2013-07-02); "
        "largest move within the window +16.0 bp",
        "- UST_10Y (DGS10): +28.0 bp after 10 trading days (2013-07-02); "
        "largest move within the window +40.0 bp",
        f"- {TP_LABEL}: +19.0 bp after 10 trading days (2013-07-02); "
        "largest move within the window +23.0 bp"])
    assert doc.doc_id == "market_episode_taper_tantrum"
    assert doc.publication == "Market episode — Taper tantrum"
    assert doc.release_date == date(2013, 7, 2)
    assert doc.fields["anchor"] == "2013-06-18"
    assert [c.text for c in doc.chunks] == [text5, text10]
    assert [c.metadata.horizon_days for c in doc.chunks] == [5, 10]
    assert [c.metadata.factor_tags for c in doc.chunks] == [TAGS, TAGS]
    assert doc.content_hash == rs.content_hash([text5, text10])


@pytest.mark.criterion("C2")
@settings(max_examples=9, deadline=None)
@given(eid=st.sampled_from(["taper_tantrum", "cpi_shock_2022", "treasury_refunding_2023"]),
       step=st.integers(min_value=1, max_value=20))
def test_property_each_new_episode_builds_its_card(rs, eid, step):
    # oracle: the Definitions computed in the test. One DGS10 series with v0 = 2.00 on the
    # anchor and 2.00 + j*step/100 on anchor + j days gives end = peak = 5*step bp at 5
    # days and 10*step bp at 10 days; the card carries the episode's name and tags.
    name, anchor, tags = rs.EPISODES[eid]
    rows = [rs.row("DGS10", (anchor + timedelta(days=j)).isoformat(), 2.00 + j * step / 100,
                   "percent") for j in range(11)]
    card = _card_for(rs, {"DGS10": rows}, eid)
    m5, m10 = card.fields["moves"]["5"]["DGS10"], card.fields["moves"]["10"]["DGS10"]
    # rel=1e-9: the spec's float tolerance (hand decimals vs binary arithmetic)
    assert (m5["end"], m5["peak"]) == pytest.approx((5.0 * step, 5.0 * step), rel=1e-9)
    # rel=1e-9: the spec's float tolerance (hand decimals vs binary arithmetic)
    assert (m10["end"], m10["peak"]) == pytest.approx((10.0 * step, 10.0 * step), rel=1e-9)
    assert card.chunks[0].metadata.factor_tags == tags + ["cross_asset_scenario"]
    assert card.publication == f"Market episode — {name}"


def _card_for(rs, rows_by_series, eid):
    with tempfile.TemporaryDirectory() as root:
        rs.write_series(Path(root), rows_by_series)
        return rs.committed_card(Path(root), eid)
