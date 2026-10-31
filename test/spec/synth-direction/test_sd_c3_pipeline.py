"""C3: QueryPipeline.synthesize applies the direction check before drop_invalid."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _synth(sd, root, *, directions=..., **cfg):
    pipe, _ = sd.build_pipeline(root, **cfg)
    kwargs = {} if directions is ... else {"directions": directions}
    syn = pipe.synthesize(sd.QUERY, **kwargs)
    assert sd.CID in {c.chunk_id for c in syn.query.chunks}
    return sd.body_of(syn)


@pytest.mark.criterion("C3")
def test_live_case_drops_usdtwd(sd, tmp_path):
    # oracle: C1 + C2 by hand on the live values. Only USDTWD (-2.32, "up") contradicts;
    # with drop_invalid it is dropped and named in synthesis_diagnostics.dropped.
    body = _synth(sd, tmp_path, directions=dict(sd.LIVE_DIRECTIONS), drop_invalid=True)
    sv = body["shock_vector"]
    assert set(sv) == {"USDJPY", "IG_OAS", "VIX"}
    for f in sv.values():
        assert sd.CODE not in f.get("validation", [])
    assert "USDTWD" in sd.dropped_names(body)


@pytest.mark.criterion("C3")
def test_live_case_annotated_without_drop_invalid(sd, tmp_path):
    # oracle: C3 by hand — drop_invalid off: all four kept, USDTWD annotated, exactly
    # one direction_mismatch warning (for USDTWD).
    body = _synth(sd, tmp_path, directions=dict(sd.LIVE_DIRECTIONS), drop_invalid=False)
    sv = body["shock_vector"]
    assert set(sv) == set(sd.LIVE)
    assert sd.CODE in sv["USDTWD"].get("validation", [])   # to_dict omits an empty list
    for n in ("USDJPY", "IG_OAS", "VIX"):
        assert sd.CODE not in sv[n].get("validation", []), n
    assert [w.get("field") for w in sd.body_warns(body)] == ["USDTWD"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_switch_off_or_no_directions_equals_today(sd):
    # oracle: C3 — direction_check false, or directions omitted, gives the same shock
    # vector as a run without directions, with no direction code. The first block
    # (check on, directions given) differs from it, so a do-nothing pipeline fails.
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
            tempfile.TemporaryDirectory() as c, tempfile.TemporaryDirectory() as d:
        checked = _synth(sd, a, directions=dict(sd.LIVE_DIRECTIONS), drop_invalid=False)
        baseline = _synth(sd, b, drop_invalid=False)
        switched_off = _synth(sd, c, directions=dict(sd.LIVE_DIRECTIONS),
                              drop_invalid=False, direction_check=False)
        none_given = _synth(sd, d, directions=None, drop_invalid=False)
    assert checked["shock_vector"] != baseline["shock_vector"]
    for body in (baseline, switched_off, none_given):
        assert body["shock_vector"] == baseline["shock_vector"]
        assert all(sd.CODE not in f.get("validation", [])
                   for f in body["shock_vector"].values())
        assert sd.body_warns(body) == []


@pytest.mark.criterion("C3")
@settings(max_examples=12, deadline=None)
@given(dirs=st.dictionaries(st.sampled_from(sorted(["USDTWD", "USDJPY", "IG_OAS", "VIX"])),
                            st.sampled_from(["up", "down"]), min_size=1))
def test_property_dropped_set_is_the_contradicted_set(sd, dirs):
    # oracle: the Definitions computed in the test over the live values: with
    # drop_invalid, the kept fields are exactly those not contradicted.
    values = {n: v for n, (v, _) in sd.LIVE.items()}
    contradicted = {n for n, d in dirs.items()
                    if (d == "up" and values[n] < 0) or (d == "down" and values[n] > 0)}
    with tempfile.TemporaryDirectory() as root:
        body = _synth(sd, root, directions=dirs, drop_invalid=True)
    assert set(body["shock_vector"]) == set(sd.LIVE) - contradicted
    assert contradicted <= sd.dropped_names(body)
