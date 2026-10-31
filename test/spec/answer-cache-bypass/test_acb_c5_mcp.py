"""C5: MCP — macro_query and macro_synthesize_shock_vector accept no_cache."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _mcp(acb, root):
    mcp = acb.mcp_server(acb.build(root, l1=acb.mem_l1()))
    for tool in acb.MCP_TOOLS:
        acb.assert_mcp_no_cache(mcp, tool)
    return mcp


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("tool", ["macro_query", "macro_synthesize_shock_vector"])
def test_tool_no_cache_after_identical_call(acb, tmp_path, tool):
    # oracle: spec C5 — no_cache: bool, default false, is in each tool's input schema;
    # after an identical call, a call with no_cache true returns cached false.
    mcp = _mcp(acb, tmp_path)
    first = acb.mcp_call(mcp, tool, {"question": acb.QUERY})
    assert first["refused"] is False
    again = acb.mcp_call(mcp, tool, {"question": acb.QUERY, "no_cache": True})
    assert again["cached"] is False


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@pytest.mark.parametrize("tool", ["macro_query", "macro_synthesize_shock_vector"])
def test_tool_default_matches_explicit_false(acb, tmp_path, tool):
    # oracle: spec C5 (default false) + C2 (false behaves as omitting it). Relation:
    # after an identical call, omitting no_cache and passing false both return cached
    # true, while passing true returns cached false.
    mcp = _mcp(acb, tmp_path)
    acb.mcp_call(mcp, tool, {"question": acb.QUERY})
    omitted = acb.mcp_call(mcp, tool, {"question": acb.QUERY})
    explicit = acb.mcp_call(mcp, tool, {"question": acb.QUERY, "no_cache": False})
    bypass = acb.mcp_call(mcp, tool, {"question": acb.QUERY, "no_cache": True})
    assert (omitted["cached"], explicit["cached"], bypass["cached"]) == (True, True, False)


@pytest.mark.criterion("C5")
@settings(max_examples=20, deadline=None)
@given(tool=st.sampled_from(["macro_query", "macro_synthesize_shock_vector"]),
       flags=st.lists(st.booleans(), min_size=2, max_size=4))
def test_property_mcp_cached_follows_the_bypass_model(acb, tool, flags):
    # oracle: spec C5 with the C2 model computed in the test (conftest expected_cached):
    # each call's cached is true iff no_cache is false and an earlier no_cache-false
    # call of the same tool stored it.
    with tempfile.TemporaryDirectory() as root:
        mcp = _mcp(acb, root)
        got = [acb.mcp_call(mcp, tool, {"question": acb.QUERY, "no_cache": f})["cached"]
               for f in flags]
    assert got == acb.expected_cached(flags)
