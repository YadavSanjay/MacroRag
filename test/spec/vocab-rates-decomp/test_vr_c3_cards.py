"""C3: cards map DFII10 and ACMTP10 to the new fields; rows come from both stores."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ingest.usstats.episodes import episode_moves, read_episode_rows

ANCHOR = date(2013, 6, 18)
DATES = ["2013-06-18", "2013-06-19", "2013-06-20", "2013-06-21", "2013-06-24", "2013-06-25"]


def _series(vr, code, vals, source="FRED"):
    return [vr.row(code, d, v, source=source) for d, v in zip(DATES, vals, strict=True)]


@pytest.mark.criterion("C3")
def test_moves_map_to_the_new_fields(vr):
    # oracle: the market-episodes Definitions by hand at k = 5 (window 06-19..06-25):
    #   DFII10 v0 0.20 -> 0.70: +50 bp end and peak, field REAL_YIELD_10Y;
    #   ACMTP10 v0 0.96 -> 1.22 (max 1.22 at 06-25): +26 bp, field TERM_PREMIUM_10Y;
    #   THREEFYTP10 stays field None.
    rows = (_series(vr, "DFII10", [0.20, 0.35, 0.45, 0.60, 0.65, 0.70])
            + _series(vr, "ACMTP10", [0.96, 1.02, 1.08, 1.15, 1.20, 1.22], source="NYFED_ACM")
            + _series(vr, "THREEFYTP10", [0.13, 0.19, 0.24, 0.30, 0.34, 0.36]))
    m = episode_moves(rows, anchor=ANCHOR, horizon=5)
    assert m["DFII10"]["field"] == "REAL_YIELD_10Y"
    assert m["ACMTP10"]["field"] == "TERM_PREMIUM_10Y"
    assert m["THREEFYTP10"]["field"] is None
    # rel=1e-9: the spec's float tolerance (hand decimals vs binary arithmetic)
    assert (m["DFII10"]["end"], m["DFII10"]["peak"]) == pytest.approx((50.0, 50.0), rel=1e-9)
    # rel=1e-9: the spec's float tolerance (hand decimals vs binary arithmetic)
    assert (m["ACMTP10"]["end"], m["ACMTP10"]["peak"]) == pytest.approx((26.0, 26.0),
                                                                        rel=1e-9)
    assert m["ACMTP10"]["unit"] == "bp"


@pytest.mark.criterion("C3")
def test_reader_unions_both_stores_and_handler_card_has_both_lines(vr):
    # oracle: C3 by hand. read_episode_rows returns FRED and ACM rows (not other sources);
    # the card built over both shows, in canonical order, the real-yield line, then the
    # ACM line, then Kim-Wright, each with its spec label.
    from utils.objectstore import InMemoryObjectStore
    fred = _series(vr, "DFII10", [0.20, 0.35, 0.45, 0.60, 0.65, 0.70]) + \
        _series(vr, "THREEFYTP10", [0.13, 0.19, 0.24, 0.30, 0.34, 0.36])
    acm = _series(vr, "ACMTP10", [0.96, 1.02, 1.08, 1.15, 1.20, 1.22], source="NYFED_ACM")
    store = InMemoryObjectStore()
    store.put_text("tier3/fred_daily/f.json", vr.doc_json("fred_daily", "f", "2026-10-15", fred))
    store.put_text("tier3/acm_daily/a.json", vr.doc_json("acm_daily", "a", "2026-10-15", acm))
    store.put_text("tier3/bea/x.json", vr.doc_json("bea", "x", "2026-10-15",
                                                   [vr.row("GDP", "2013-06-19", 1.0)]))
    rows = read_episode_rows(store)
    assert sorted((r["series_code"], r["date"]) for r in rows) == \
        sorted((r["series_code"], r["date"]) for r in fred + acm)

    with tempfile.TemporaryDirectory() as root:
        vr.write(Path(root), {
            "tier3/fred_daily/f.json": vr.doc_json("fred_daily", "f", "2026-10-15", fred),
            "tier3/acm_daily/a.json": vr.doc_json("acm_daily", "a", "2026-10-15", acm)})
        cards = vr.run_cards(Path(root), [{"id": "taper", "name": "Taper", "anchor": ANCHOR,
                                           "factor_tags": ["term_premium"],
                                           "entity_tags": []}])
    assert len(cards) == 1
    lines = cards[0].chunks[0].text.split("\n")[1:]
    labels = [ln.split(":")[0][2:] for ln in lines]
    assert labels == [vr.REAL_LABEL, vr.TP_LABEL, vr.KW_LABEL]


@pytest.mark.criterion("C3")
@settings(max_examples=20, deadline=None)
@given(step=st.integers(min_value=1, max_value=30),
       later=st.booleans())
def test_property_later_retrieval_wins_across_stores(vr, step, later):
    # oracle: C3 — on a duplicate (series, date) the document retrieved later wins, also
    # across the two prefixes: an ACMTP10 row duplicated in fred_daily (retrieved earlier
    # or later than the acm_daily copy) resolves to the later document's value.
    from utils.objectstore import InMemoryObjectStore
    a = [vr.row("ACMTP10", "2013-06-19", 1.0, source="NYFED_ACM")]
    b = [vr.row("ACMTP10", "2013-06-19", 1.0 + step / 100, source="NYFED_ACM")]
    ra, rb = ("2026-10-15", "2026-10-10") if later else ("2026-10-10", "2026-10-15")
    store = InMemoryObjectStore()
    store.put_text("tier3/acm_daily/a.json", vr.doc_json("acm_daily", "a", ra, a))
    store.put_text("tier3/fred_daily/b.json", vr.doc_json("fred_daily", "b", rb, b))
    got = [r for r in read_episode_rows(store) if r["series_code"] == "ACMTP10"]
    assert len(got) == 1
    # rel=1e-12: the stored value passes through unchanged
    assert got[0]["value"] == pytest.approx(1.0 if later else 1.0 + step / 100, rel=1e-12)
