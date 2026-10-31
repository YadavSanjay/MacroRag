"""C1: build_episode_document measures an episode at each of its configured horizons."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ingest.usstats.episodes import build_episode_document


@pytest.mark.criterion("C1")
def test_lehman_at_one_five_ten_unsorted(ech):
    # oracle: the Definitions applied by hand to the Lehman rows (conftest comment block).
    # horizons [10, 1, 5] are used ascending: chunks 1, 5, 10 with seq 0, 1, 2; the
    # 1-day header and lines read "1 trading day"; the 5- and 10-day texts are exactly
    # today's; moves keyed "1", "5", "10"; release_date 2008-09-29 (USDJPY's 10-day
    # last date) on the document and every chunk; content_hash over the three texts.
    doc = build_episode_document(ech.lehman([10, 1, 5]), ech.lehman_rows(), today=ech.TODAY)
    ech.assert_lehman(doc, [1, 5, 10])
    assert doc.chunks[0].text.splitlines()[0] == (
        "Market episode: Lehman Brothers bankruptcy. Realized moves over the 1 trading day "
        "after the 2008-09-12 close, from FRED daily data.")
    assert doc.chunks[1].text == ech.LEHMAN_TEXT5_TODAY
    assert doc.release_date == date(2008, 9, 29)


@pytest.mark.criterion("C1")
def test_horizon_without_moves_and_none_case(ech):
    # oracle: by hand. horizons [12, 3]: no series has 12 rows after the anchor, so
    # moves["12"] == {} and the card is one chunk (seq 0, horizon 3); its release_date
    # is 2008-09-18 (USDJPY's 3-day last date), not a 5- or 10-day date.
    # horizons [11, 12]: no series moves at any horizon -> None.
    # Without the key the card is the 5/10 card (default [5, 10]).
    doc = build_episode_document(ech.lehman([12, 3]), ech.lehman_rows(), today=ech.TODAY)
    ech.assert_lehman(doc, [3, 12])
    assert doc.release_date == date(2008, 9, 18)
    assert build_episode_document(ech.lehman([11, 12]), ech.lehman_rows(),
                                  today=ech.TODAY) is None
    default = build_episode_document(ech.lehman(drop=True), ech.lehman_rows(),
                                     today=ech.TODAY)
    ech.assert_lehman(default, [5, 10])


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(perm=st.permutations([1, 5, 10]))
def test_horizon_order_does_not_matter_and_default_is_five_ten(ech, perm):
    # oracle: the Definitions — horizons are used in ascending order whatever their
    # configured order, and the default is [5, 10]. Relations: every order of [1, 5, 10]
    # gives the same document (the hand-worked 1/5/10 card), and horizons [5, 10]
    # gives the same document as no key.
    rows = ech.lehman_rows()
    doc = build_episode_document(ech.lehman(list(perm)), rows, today=ech.TODAY)
    ech.assert_lehman(doc, [1, 5, 10])
    ref = build_episode_document(ech.lehman([1, 5, 10]), rows, today=ech.TODAY)
    assert doc.model_dump() == ref.model_dump()
    five_ten = build_episode_document(ech.lehman([5, 10]), rows, today=ech.TODAY)
    no_key = build_episode_document(ech.lehman(drop=True), rows, today=ech.TODAY)
    assert five_ten.model_dump() == no_key.model_dump()


_SVB = {"id": "svb", "name": "Silicon Valley Bank failure", "anchor": date(2023, 3, 8),
        "factor_tags": ["systemic"], "entity_tags": []}


@pytest.mark.criterion("C1")
@given(steps=st.lists(st.integers(min_value=-400, max_value=400), max_size=16),
       horizons=st.lists(st.integers(min_value=1, max_value=16), min_size=1, max_size=4,
                         unique=True))
def test_property_card_at_any_horizons_closed_form(ech, steps, horizons):
    # oracle: closed form from the Definitions for one VIXCLS series (v_0 = 20 on the
    # anchor, row j on anchor + j + 1 days = 20 + c_j/4, exact quarter steps). Horizon k
    # has a move iff there are >= k rows: end = c_{k-1}/4, peak = the first change of
    # maximal |c|, last date anchor + k. One chunk per horizon with a move, ascending;
    # None when there is none; release_date = anchor + the largest horizon with a move.
    a = _SVB["anchor"]
    rows = [ech.row("VIXCLS", a.isoformat(), 20.0, "index_points")]
    rows += [ech.row("VIXCLS", (a + timedelta(days=j + 1)).isoformat(), 20.0 + c / 4,
                     "index_points") for j, c in enumerate(steps)]
    ep = dict(_SVB, horizons=list(horizons))
    doc = build_episode_document(ep, rows, today=ech.TODAY)
    chunks, moves = [], {}
    for k in sorted(horizons):
        if len(steps) < k:
            moves[str(k)] = {}
            continue
        win = [c / 4 for c in steps[:k]]
        end = win[-1]
        peak = next(x for x in win if abs(x) == max(abs(y) for y in win))
        last = (a + timedelta(days=k)).isoformat()
        moves[str(k)] = {"VIXCLS": ech.move("VIX", "pts", end, peak, last)}
        chunks.append((k, "\n".join([
            ech.header(_SVB["name"], k, a),
            ech.line("VIX (VIXCLS)", f"{end:+.2f}", "pts", f"{peak:+.2f}", k, last)])))
    if not chunks:
        assert doc is None
        return
    ech.assert_card(doc, ep=_SVB, today=ech.TODAY, release=a + timedelta(days=chunks[-1][0]),
                    moves=moves, chunks=chunks,
                    factor_tags=["systemic", "cross_asset_scenario"])
