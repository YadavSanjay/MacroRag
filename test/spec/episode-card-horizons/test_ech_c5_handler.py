"""C5: the committed endpoint, run by its registry handler, emits one chunk per horizon."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# oracle: the spec's C4 table (name, anchor, horizons), copied verbatim.
EPISODES = {
    "ai_peak_to_trough_2022": ("2021-22 AI and growth-equity peak-to-trough",
                               date(2021, 11, 29), [5, 10, 221]),
    "hawkish_repricing_2022": ("Aug-Oct 2022 hawkish real-rate repricing",
                               date(2022, 8, 16), [5, 10, 42]),
    "ai_rotation_2024": ("July 2024 rotation out of AI leaders", date(2024, 7, 10), [5, 10]),
    "deepseek": ("DeepSeek AI selloff", date(2025, 1, 24), [1, 5, 10]),
}


def _card(ech, eid: str):
    """Run the committed endpoint on the spec's synthetic VIXCLS series for ``eid``:
    v_0 = 20.00 on the anchor and 20 + j on anchor + j calendar days, j = 1..max horizon."""
    _, a, hs = EPISODES[eid]
    rows = [ech.row("VIXCLS", (a + timedelta(days=j)).isoformat(), 20.0 + j, "index_points")
            for j in range(max(hs) + 1)]
    with tempfile.TemporaryDirectory() as root:
        ech.write_series(Path(root), "VIXCLS", rows)
        cards = ech.committed_cards(Path(root))
    # The long series can reach other episodes' anchors too; pick this episode's card.
    doc_id = f"market_episode_{eid}"
    assert doc_id in cards, sorted(cards)
    return cards[doc_id]


def _assert_card(ech, eid: str, doc) -> None:
    # oracle: the Definitions by hand. With v = 20 + j the window of k rows ends on
    # anchor + k at 20 + k, every change is positive and grows, so end = peak = +k.00 pts.
    name, a, hs = EPISODES[eid]
    assert [c.metadata.horizon_days for c in doc.chunks] == sorted(hs)
    for c, k in zip(doc.chunks, sorted(hs), strict=True):
        dk = (a + timedelta(days=k)).isoformat()
        assert c.text == "\n".join([
            f"Market episode: {name}. Realized moves over the {k} trading "
            f"{ech.day_word(k)} after the {a.isoformat()} close, from FRED daily data.",
            f"- VIX (VIXCLS): +{k}.00 pts after {k} trading {ech.day_word(k)} ({dk}); "
            f"largest move within the window +{k}.00 pts"]), k
        assert c.metadata.release_date == a + timedelta(days=max(hs))
    assert doc.release_date == a + timedelta(days=max(hs))


@pytest.mark.criterion("C5")
def test_deepseek_and_221_day_cards(ech):
    # oracle: the Definitions by hand.
    # deepseek (anchor 2025-01-24, horizons 1, 5, 10): three chunks; the first reads
    #   "... over the 1 trading day after the 2025-01-24 close ..." and
    #   "- VIX (VIXCLS): +1.00 pts after 1 trading day (2025-01-25); largest move within
    #   the window +1.00 pts"; release_date 2025-02-03 (anchor + 10).
    # ai_peak_to_trough_2022 (anchor 2021-11-29, horizons 5, 10, 221): the last chunk
    #   ends on 2021-11-29 + 221 days = 2022-07-08 at +221.00 pts, and every chunk is
    #   dated 2022-07-08.
    deepseek = _card(ech, "deepseek")
    _assert_card(ech, "deepseek", deepseek)
    assert deepseek.chunks[0].text == (
        "Market episode: DeepSeek AI selloff. Realized moves over the 1 trading day after "
        "the 2025-01-24 close, from FRED daily data.\n"
        "- VIX (VIXCLS): +1.00 pts after 1 trading day (2025-01-25); "
        "largest move within the window +1.00 pts")
    assert deepseek.release_date == date(2025, 2, 3)
    peak = _card(ech, "ai_peak_to_trough_2022")
    _assert_card(ech, "ai_peak_to_trough_2022", peak)
    assert peak.chunks[-1].text.endswith(
        "- VIX (VIXCLS): +221.00 pts after 221 trading days (2022-07-08); "
        "largest move within the window +221.00 pts")
    assert {c.metadata.release_date for c in peak.chunks} == {date(2022, 7, 8)}


@pytest.mark.criterion("C5")
@settings(deadline=None)
@given(eid=st.sampled_from(sorted(EPISODES)))
def test_property_every_c4_episode_one_chunk_per_horizon(ech, eid):
    # oracle: the C4 table + the Definitions by hand (see _assert_card): for every C4
    # episode, one chunk per configured horizon, ascending, each with the closed-form line.
    _assert_card(ech, eid, _card(ech, eid))
