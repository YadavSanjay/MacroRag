"""C2: QueryPipeline.synthesize passes the directions into the synthesize prompt."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

FIELDS = ["USDTWD", "USDJPY", "IG_OAS", "VIX", "UST_10Y"]


@pytest.mark.criterion("C2")
def test_pipeline_prompt_carries_the_line(sdp, tmp_path):
    # oracle: C1's Definitions by hand for {USDTWD up, UST_10Y down}: SHOCK_FIELDS order
    # puts UST_10Y before USDTWD. With horizon 10 the line follows the horizon line.
    line = ("- Expected directions (from the scenario; ground each field on evidence "
            "that moved this way): UST_10Y down (-, positive means yields rise); USDTWD "
            "up (+, positive means TWD depreciates).")
    prompts = sdp.pipeline_prompts(tmp_path, horizon_days=10,
                                   directions={"USDTWD": "up", "UST_10Y": "down"})
    assert len(prompts) == 1
    assert sdp.HORIZON_10 + "\n" + line + "\n\nQUESTION:" in prompts[0]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_without_directions_no_line_with_them_one(sdp):
    # oracle: C2. Relation: the same request with and without directions differs
    # exactly by the directions line inside CONSTRAINTS.
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        without = sdp.pipeline_prompts(a, horizon_days=10)
        with_dirs = sdp.pipeline_prompts(b, horizon_days=10, directions={"VIX": "up"})
    line = sdp.expected_line({"VIX": "up"})
    assert sdp.PREFIX not in without[0]
    assert with_dirs[0] == without[0].replace(sdp.HORIZON_10,
                                              sdp.HORIZON_10 + "\n" + line, 1)


@pytest.mark.criterion("C2")
@settings(max_examples=8, deadline=None)
@given(dirs=st.dictionaries(st.sampled_from(FIELDS), st.sampled_from(["up", "down"]),
                            min_size=1))
def test_property_prompt_line_matches_the_definitions(sdp, dirs):
    # oracle: C1's Definitions computed in the test (the hand-copied table).
    with tempfile.TemporaryDirectory() as root:
        prompts = sdp.pipeline_prompts(root, directions=dirs)
    assert len(prompts) == 1
    assert "ADDITIONAL CONSTRAINTS:\n" + sdp.expected_line(dirs) + "\n\nQUESTION:" \
        in prompts[0]
