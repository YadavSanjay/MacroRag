"""C2: the committed endpoint builds the russia_ukraine card from FRED rows."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

BAA_STANDIN = ("IG_OAS stand-in (BAA10Y, Moody's Baa yield minus 10-year Treasury; "
               "a yield spread, not an OAS)")
TAGS = ["geopolitical_risk", "sanctions_regime", "flight_to_quality", "credit_spread",
        "cross_asset_scenario"]


def _card(ru, rows_by_series):
    with tempfile.TemporaryDirectory() as root:
        ru.write_series(Path(root), rows_by_series)
        return ru.committed_card(Path(root))


@pytest.mark.criterion("C2")
def test_card_from_the_notes_rows(ru):
    # oracle: the market-episodes Definitions applied by hand to the spec's Notes rows.
    # Anchor 2022-02-23; window days 1..5 end 2022-03-02, days 1..10 end 2022-03-09.
    #   VIXCLS v0 31.02 (pts = v - v0): -0.70 -3.43 -0.87 +2.30 -0.28 | -0.54 +0.96
    #          +5.43 +4.11 +1.43 -> 5d end -0.28 peak -3.43; 10d end +1.43 peak +5.43
    #   DGS10 v0 1.99 (bp = (v - v0) x 100): -3 -2 -16 -27 -13 | -13 -25 -21 -13 -5
    #          -> 5d end -13 peak -27; 10d end -5 peak -27
    #   BAA10Y v0 2.10 (bp), no BAMLC0A0CM rows -> IG_OAS stand-in:
    #          +14 +10 +24 +15 +14 | +14 +21 +22 +25 +22 -> 5d +14/+24; 10d +22/+25
    #   DEXTAUS v0 27.86 (pct = (v / v0 - 1) x 100): 5d end 28.04 -> 0.18/27.86,
    #          peak 28.13 -> 0.27/27.86; 10d end = peak 28.41 -> 0.55/27.86
    doc = _card(ru, ru.notes_rows())
    d5, d10 = "2022-03-02", "2022-03-09"
    moves5 = {
        "VIXCLS": ru.move("VIX", "pts", -0.28, -3.43, d5),
        "DGS10": ru.move("UST_10Y", "bp", -13.0, -27.0, d5),
        "BAA10Y": ru.move("IG_OAS", "bp", 14.0, 24.0, d5),
        "DEXTAUS": ru.move("USDTWD", "pct", 0.18 / 27.86 * 100, 0.27 / 27.86 * 100, d5),
    }
    moves10 = {
        "VIXCLS": ru.move("VIX", "pts", 1.43, 5.43, d10),
        "DGS10": ru.move("UST_10Y", "bp", -5.0, -27.0, d10),
        "BAA10Y": ru.move("IG_OAS", "bp", 22.0, 25.0, d10),
        "DEXTAUS": ru.move("USDTWD", "pct", 0.55 / 27.86 * 100, 0.55 / 27.86 * 100, d10),
    }
    hdr = ("Market episode: Russian invasion of Ukraine. Realized moves over the {k} "
           "trading days after the 2022-02-23 close, from FRED daily data.")
    text5 = "\n".join([
        hdr.format(k=5),
        "- VIX (VIXCLS): -0.28 pts after 5 trading days (2022-03-02); "
        "largest move within the window -3.43 pts",
        "- UST_10Y (DGS10): -13.0 bp after 5 trading days (2022-03-02); "
        "largest move within the window -27.0 bp",
        f"- {BAA_STANDIN}: +14.0 bp after 5 trading days (2022-03-02); "
        "largest move within the window +24.0 bp",
        "- USDTWD (DEXTAUS): +0.65 pct after 5 trading days (2022-03-02); "
        "largest move within the window +0.97 pct",
    ])
    text10 = "\n".join([
        hdr.format(k=10),
        "- VIX (VIXCLS): +1.43 pts after 10 trading days (2022-03-09); "
        "largest move within the window +5.43 pts",
        "- UST_10Y (DGS10): -5.0 bp after 10 trading days (2022-03-09); "
        "largest move within the window -27.0 bp",
        f"- {BAA_STANDIN}: +22.0 bp after 10 trading days (2022-03-09); "
        "largest move within the window +25.0 bp",
        "- USDTWD (DEXTAUS): +1.97 pct after 10 trading days (2022-03-09); "
        "largest move within the window +1.97 pct",
    ])
    release = date(2022, 3, 9)

    assert doc.doc_id == "market_episode_russia_ukraine"
    assert doc.doc_type == "market_episode"
    assert doc.url == "episode://russia_ukraine"
    assert doc.publisher == "MacroRAG (derived from FRED)"
    assert doc.publication == "Market episode — Russian invasion of Ukraine"
    assert doc.retrieval_date == ru.TODAY
    assert doc.release_date == release
    assert doc.timeseries == []
    assert doc.fields["episode"] == "russia_ukraine"
    assert doc.fields["anchor"] == "2022-02-23"
    ru.assert_moves(doc.fields["moves"]["5"], moves5)
    ru.assert_moves(doc.fields["moves"]["10"], moves10)
    assert [c.chunk_id for c in doc.chunks] == ["market_episode_russia_ukraine::000",
                                                "market_episode_russia_ukraine::001"]
    assert [c.text for c in doc.chunks] == [text5, text10]
    for c, k in zip(doc.chunks, (5, 10), strict=True):
        assert c.metadata.horizon_days == k
        assert c.metadata.doc_type == "market_episode"
        assert c.metadata.release_date == release
        assert c.metadata.factor_tags == TAGS
        assert c.metadata.entity_tags == []
    assert doc.content_hash == ru.content_hash([text5, text10])


@pytest.mark.criterion("C2")
@settings(max_examples=25, deadline=None)
@given(cents=st.lists(st.integers(min_value=900, max_value=8000), min_size=11,
                      max_size=11))
def test_property_vix_moves_follow_the_definitions(ru, cents):
    # oracle: the market-episodes Definitions computed in the test. With only VIXCLS
    # rows on the Notes dates, v0 = the 2022-02-23 value, change_j = v_j - v0, end at k
    # is change_k, peak is the change of largest |.| among days 1..k (earliest on a tie).
    vals = [c / 100 for c in cents]
    rows = [ru.row("VIXCLS", d, v, "index_points") for d, v in zip(ru.DATES, vals,
                                                                   strict=True)]
    doc = _card(ru, {"VIXCLS": rows})
    changes = [v - vals[0] for v in vals[1:]]

    def expected(k: int) -> dict:
        window = changes[:k]
        peak = max(window, key=abs)   # max keeps the first of equal |.|: earliest wins
        return {"VIXCLS": ru.move("VIX", "pts", window[-1], peak, ru.DATES[k])}

    ru.assert_moves(doc.fields["moves"]["5"], expected(5))
    ru.assert_moves(doc.fields["moves"]["10"], expected(10))
    assert doc.release_date == date(2022, 3, 9)
    assert [c.metadata.factor_tags for c in doc.chunks] == [TAGS, TAGS]
