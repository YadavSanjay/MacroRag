"""C2: build_episode_document turns the moves into a two-chunk episode card."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ingest.usstats.episodes import build_episode_document


@pytest.mark.criterion("C2")
def test_lehman_card(me):
    # oracle: the Definitions applied by hand to the Lehman rows (conftest comment
    # block): lines in canonical order VIX, UST_2Y, the BAA10Y stand-in (5 only),
    # USDJPY; texts LEHMAN_TEXT5/10; release_date 2008-09-29 (USDJPY's 10-day last
    # date); factor tags + cross_asset_scenario appended; content_hash = sha256 of
    # the two texts joined by "\n\n".
    doc = build_episode_document(dict(me.LEHMAN), me.lehman_rows(), today=me.TODAY)
    me.assert_lehman_card(doc)


@pytest.mark.criterion("C2")
def test_five_day_only_card_existing_tag_and_none_cases(me):
    # oracle: by hand. deepseek, anchor Friday 2025-01-24, dates after 01-27..02-03.
    # DGS5 v0 4.40: 4.30 4.32 4.35 4.33 4.34 4.31 -> -10 -8 -5 -7 -6 (-9): k5 end
    # -6.0 peak -10.0 last 01-31; six rows -> no move at 10, so one chunk (::000,
    # horizon 5) and moves["10"] == {}. DGS10's latest row is 01-16 (8 days) -> no
    # move. cross_asset_scenario is already a tag: order kept, not duplicated.
    # No rows, only stale rows, or only non-canonical rows -> None.
    ep = {"id": "deepseek", "name": "DeepSeek AI selloff", "anchor": date(2025, 1, 24),
          "factor_tags": ["cross_asset_scenario", "equity_vol"],
          "entity_tags": ["ai", "semiconductors"]}
    d = ["2025-01-27", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
         "2025-02-03"]
    dgs5 = [me.row("DGS5", "2025-01-24", 4.40, "percent")] + [
        me.row("DGS5", x, v, "percent")
        for x, v in zip(d, [4.30, 4.32, 4.35, 4.33, 4.34, 4.31], strict=True)]
    stale = [me.row("DGS10", "2025-01-16", 4.60, "percent")] + [
        me.row("DGS10", x, 4.50, "percent") for x in d]
    text = "\n".join([
        "Market episode: DeepSeek AI selloff. Realized moves over the 5 trading days "
        "after the 2025-01-24 close, from FRED daily data.",
        "- UST_5Y (DGS5): -6.0 bp after 5 trading days (2025-01-31); "
        "largest move within the window -10.0 bp",
    ])
    doc = build_episode_document(ep, dgs5 + stale, today=me.TODAY)
    me.assert_card(doc, ep=ep, today=me.TODAY, release=date(2025, 1, 31),
                   moves5={"DGS5": me.move("UST_5Y", "bp", -6.0, -10.0, "2025-01-31")},
                   moves10={}, chunks=[(5, text)],
                   factor_tags=["cross_asset_scenario", "equity_vol"])

    other = [me.row("DGS1", "2025-01-24", 4.0, "percent")] + [
        me.row("DGS1", x, 4.1, "percent") for x in d]
    assert build_episode_document(ep, [], today=me.TODAY) is None
    assert build_episode_document(ep, stale, today=me.TODAY) is None
    assert build_episode_document(ep, other, today=me.TODAY) is None


_SVB = {"id": "svb", "name": "Silicon Valley Bank failure", "anchor": date(2023, 3, 8),
        "factor_tags": ["systemic"], "entity_tags": []}


@pytest.mark.criterion("C2")
@given(steps=st.lists(st.integers(min_value=-400, max_value=400), max_size=12),
       today=st.dates(min_value=date(2024, 1, 1), max_value=date(2030, 12, 31)))
def test_property_card_shape_closed_form(me, steps, today):
    # oracle: closed form from the Definitions for one VIXCLS series (v0 = 20, row j
    # on anchor + j + 1 days = 20 + c_j/4, exact quarter steps). Fewer than 5 rows ->
    # None; 5..9 -> one chunk (5); >= 10 -> two (5, 10). Each line is the spec's
    # f-string with 2 decimals; peak = first change of maximal |c|; release_date =
    # the last chunk's window end; content_hash = sha256 of the texts joined "\n\n".
    a = _SVB["anchor"]
    rows = [me.row("VIXCLS", a.isoformat(), 20.0, "index_points")]
    rows += [me.row("VIXCLS", (a + timedelta(days=j + 1)).isoformat(), 20.0 + c / 4,
                    "index_points") for j, c in enumerate(steps)]
    doc = build_episode_document(dict(_SVB), rows, today=today)
    if len(steps) < 5:
        assert doc is None
        return
    chunks, moves = [], {}
    for k in (5, 10):
        if len(steps) < k:
            moves[str(k)] = {}
            continue
        win = [c / 4 for c in steps[:k]]
        end = win[-1]
        peak = next(x for x in win if abs(x) == max(abs(y) for y in win))
        last = (a + timedelta(days=k)).isoformat()
        moves[str(k)] = {"VIXCLS": me.move("VIX", "pts", end, peak, last)}
        chunks.append((k, "\n".join([
            me.HDR.format(name=_SVB["name"], k=k, anchor=a.isoformat()),
            f"- VIX (VIXCLS): {end:+.2f} pts after {k} trading days ({last}); "
            f"largest move within the window {peak:+.2f} pts"])))
    release = a + timedelta(days=chunks[-1][0])
    me.assert_card(doc, ep=_SVB, today=today, release=release, moves5=moves["5"],
                   moves10=moves["10"], chunks=chunks,
                   factor_tags=["systemic", "cross_asset_scenario"])


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(data=st.data())
def test_property_same_inputs_same_document_any_row_order(me, data):
    # oracle: the hand-worked Lehman card (conftest). The Definitions sort by date
    # per series, so any permutation of the rows gives that same card, and building
    # twice from the same inputs gives identical documents.
    rows = data.draw(st.permutations(me.lehman_rows()))
    first = build_episode_document(dict(me.LEHMAN), list(rows), today=me.TODAY)
    me.assert_lehman_card(first)
    again = build_episode_document(dict(me.LEHMAN), list(rows), today=me.TODAY)
    assert again.model_dump() == first.model_dump()
