"""C1: synthesize_shock_vector states the requested directions in CONSTRAINTS."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.synthesizer import direction_line

HEAD, TAIL = "ADDITIONAL CONSTRAINTS:\n", "\n\nQUESTION:"


def _section(block: str) -> str:
    return HEAD + block + TAIL


@pytest.mark.criterion("C1")
def test_direction_line_exact_order_and_skips(sdp):
    # oracle: the Definitions by hand. SHOCK_FIELDS order puts UST_10Y, then VIX, then
    # USDTWD; "vix" is upper-cased; NOPE (not a field) and IG_OAS "sideways" (not
    # up/down) are skipped. Conventions from the published table (guide 05 §5).
    line = direction_line({"USDTWD": "up", "vix": "up", "UST_10Y": "down",
                           "NOPE": "up", "IG_OAS": "sideways"})
    assert line == (
        "- Expected directions (from the scenario; ground each field on evidence that "
        "moved this way): UST_10Y down (-, positive means yields rise); VIX up (+, "
        "positive means equity vol rises); USDTWD up (+, positive means TWD depreciates).")


@pytest.mark.criterion("C1")
def test_block_appends_the_line_after_todays_lines(sdp):
    # oracle: the CONSTRAINTS rule by hand. With horizon 10 and fields [VIX, IG_OAS],
    # today's block is the horizon line then the fields line; the directions line is
    # appended after them. Everything else in the prompt is unchanged.
    today = "\n".join([sdp.HORIZON_10, sdp.fields_line(["VIX", "IG_OAS"])])
    line = ("- Expected directions (from the scenario; ground each field on evidence "
            "that moved this way): IG_OAS up (+, positive means spreads widen); VIX up "
            "(+, positive means equity vol rises).")
    base = sdp.synth_prompt(horizon_days=10, fields=["VIX", "IG_OAS"])
    assert _section(today) in base
    got = sdp.synth_prompt(horizon_days=10, fields=["VIX", "IG_OAS"],
                           directions={"VIX": "up", "IG_OAS": "up"})
    assert got == base.replace(_section(today), _section(today + "\n" + line))


@pytest.mark.criterion("C1")
def test_line_alone_replaces_none_and_empty_is_inert(sdp):
    # oracle: the CONSTRAINTS rule by hand. With no horizon and no fields today's block
    # is "(none)"; with directions only, the block is the line alone. None, {} and a
    # dict with no valid entry render today's block exactly.
    base = sdp.synth_prompt()
    assert _section("(none)") in base
    line = ("- Expected directions (from the scenario; ground each field on evidence "
            "that moved this way): USDJPY down (-, positive means JPY depreciates).")
    got = sdp.synth_prompt(directions={"USDJPY": "down"})
    assert got == base.replace(_section("(none)"), _section(line))
    assert direction_line({"USDJPY": "down"}) == line
    for empty in (None, {}, {"NOPE": "up", "VIX": "flat"}):
        assert direction_line(empty) is None
        assert sdp.synth_prompt(directions=empty) == base


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None)
@given(directions=st.dictionaries(
    st.sampled_from(["UST_2Y", "ust_30y", "IG_OAS", "HY_OAS", "MOVE", "VIX",
                     "USDTWD", "usdjpy", "XCCY_BASIS", "NOPE", "SPX"]),
    st.sampled_from(["up", "down", "flat", "UP"])))
def test_property_line_matches_the_definitions(sdp, directions):
    # oracle: the Definitions computed in the test from the hand-copied table. A control
    # entry (HY_OAS up) guarantees a line, so a do-nothing rendering fails. No two keys
    # differ only in case (the spec leaves that case open).
    d = dict(directions)
    d.pop("HY_OAS", None)
    d["HY_OAS"] = "up"
    assert direction_line(d) == sdp.expected_line(d)
