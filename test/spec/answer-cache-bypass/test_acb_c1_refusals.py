"""C1: neither cache stores a refused result."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.mark.criterion("C1")
def test_refusal_is_not_cached_by_either_level(acb, tmp_path):
    # oracle: spec C1 — a refusal asked twice is refused again and not served from
    # L1 (in-memory L1, no L2) nor from L2 (NullCache L1, in-memory L2); with the floor
    # at its default the second call is cached, as today.
    pipe = acb.build(tmp_path / "l1", l1=acb.mem_l1(), floor=acb.REFUSE_FLOOR)
    first, second = pipe.answer(acb.QUERY), pipe.answer(acb.QUERY)
    assert first.answer.refused is True and second.answer.refused is True
    assert second.cached is False, "L1 served a cached refusal"

    pipe = acb.build(tmp_path / "l2", l2=acb.mem_l2(), floor=acb.REFUSE_FLOOR)
    first, second = pipe.answer(acb.QUERY), pipe.answer(acb.QUERY)
    assert first.answer.refused is True and second.answer.refused is True
    assert second.cached is False, "L2 served a cached refusal"

    pipe = acb.build(tmp_path / "default", l1=acb.mem_l1())
    first, second = pipe.answer(acb.QUERY), pipe.answer(acb.QUERY)
    assert first.answer.refused is False
    assert second.cached is True


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_refusal_does_not_outlive_a_lowered_floor(acb, tmp_path):
    # oracle: spec C1. Relation: the same question on the same pipeline, refused under a
    # high floor, then asked again with the floor back at its default, must be answered
    # fresh (not refused, not cached) because the refusal was never stored; a third call
    # is then cached.
    from utils.config import RagSettings
    default_floor = RagSettings().retrieval.min_rerank_score
    pipe = acb.build(tmp_path, l1=acb.mem_l1(), l2=acb.mem_l2(), floor=acb.REFUSE_FLOOR)
    refused = pipe.answer(acb.QUERY)
    assert refused.answer.refused is True
    pipe.s.retrieval.min_rerank_score = default_floor
    fresh = pipe.answer(acb.QUERY)
    assert fresh.answer.refused is False
    assert fresh.cached is False
    assert pipe.answer(acb.QUERY).cached is True


@pytest.mark.criterion("C1")
@settings(max_examples=12, deadline=None)
@given(levels=st.sampled_from(["l1", "l2", "both"]), query=st.sampled_from(
    ["how did treasury yields behave", "what happened to treasury yields",
     "treasury yields across advanced economies", "did markets price cuts"]),
    repeats=st.integers(min_value=2, max_value=4))
def test_property_repeated_refusals_are_never_cached(acb, levels, query, repeats):
    # oracle: spec C1 — for any cache configuration, question and number of repeats,
    # every call under the refusing floor is refused and none is cached.
    l1, l2 = acb.caches(levels)
    with tempfile.TemporaryDirectory() as root:
        pipe = acb.build(root, l1=l1, l2=l2, floor=acb.REFUSE_FLOOR)
        results = [pipe.answer(query) for _ in range(repeats)]
    assert all(r.answer.refused for r in results)
    assert [r.cached for r in results] == [False] * repeats
