"""C4: REST and MCP pass synthesis directions through, names validated like fields."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _rest(sd, root, directions):
    pipe, s = sd.build_pipeline(root, drop_invalid=True)
    client = sd.rest_client(pipe, s)
    return client.post("/query", json={"question": sd.QUERY, "mode": "synthesize",
                                       "synthesis": {"directions": directions}})


@pytest.mark.criterion("C4")
def test_rest_lowercase_keys_reach_the_check(sd, tmp_path):
    # oracle: C4 (keys upper-cased like fields) + C3 by hand: "usdtwd": "up" against
    # -2.32 contradicts, so with drop_invalid USDTWD is dropped; the others agree.
    r = _rest(sd, tmp_path, {k.lower(): v for k, v in sd.LIVE_DIRECTIONS.items()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["shock_vector"]) == {"USDJPY", "IG_OAS", "VIX"}
    assert "USDTWD" in sd.dropped_names(body)


@pytest.mark.criterion("C4")
def test_rest_rejects_unknown_key_400_and_bad_value_422(sd, tmp_path):
    # oracle: C4 — an unknown shock-field key returns 400 naming it (the fields rule);
    # a value other than up/down returns 422 (request validation).
    r = _rest(sd, tmp_path, {"NOT_A_FIELD": "up", "VIX": "up"})
    assert r.status_code == 400, r.text
    assert "NOT_A_FIELD" in r.text
    r = _rest(sd, tmp_path, {"VIX": "sideways"})
    assert r.status_code == 422, r.text


@pytest.mark.criterion("C4")
def test_mcp_passes_directions_and_rejects_bad_ones(sd, tmp_path):
    # oracle: C4 — MCP mirrors REST: the live directions drop USDTWD; an unknown key or
    # a bad value makes the tool call fail with an error naming it.
    pipe, s = sd.build_pipeline(tmp_path, drop_invalid=True)
    body = sd.mcp_call(pipe, s, {"question": sd.QUERY,
                                 "directions": dict(sd.LIVE_DIRECTIONS)})
    assert set(body["shock_vector"]) == {"USDJPY", "IG_OAS", "VIX"}
    assert "USDTWD" in sd.dropped_names(body)
    with pytest.raises(Exception, match="NOT_A_FIELD"):
        sd.mcp_call(pipe, s, {"question": sd.QUERY, "directions": {"NOT_A_FIELD": "up"}})
    with pytest.raises(Exception, match="sideways"):
        sd.mcp_call(pipe, s, {"question": sd.QUERY, "directions": {"VIX": "sideways"}})


@pytest.mark.criterion("C4")
@settings(max_examples=8, deadline=None)
@given(mask=st.lists(st.booleans(), min_size=6, max_size=6))
def test_property_key_case_does_not_matter(sd, mask):
    # oracle: C4 — keys are upper-cased like fields, so any mix of letter case in
    # "usdtwd" gives the same result as "USDTWD": USDTWD dropped (-2.32 vs "up").
    key = "".join(ch.lower() if low else ch for ch, low in zip("USDTWD", mask, strict=True))
    with tempfile.TemporaryDirectory() as root:
        r = _rest(sd, root, {key: "up"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "USDTWD" not in body["shock_vector"]
    assert "USDTWD" in sd.dropped_names(body)
