"""C3: QueryPipeline.synthesize passes no_cache to its query step."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _pipe(acb, root):
    from pipelines.query_pipeline import QueryPipeline
    acb.assert_keyword(QueryPipeline.synthesize)
    return acb.build(root, l1=acb.mem_l1())


@pytest.mark.criterion("C3")
def test_synthesize_no_cache_neither_reads_nor_writes(acb, tmp_path):
    # oracle: spec C3 — synthesize(q) then synthesize(q, no_cache=True): the second's
    # query is not cached; synthesize(q, no_cache=True) then synthesize(q): the second's
    # query is not cached; without the flag the second synthesize(q) is cached, as today.
    pipe = _pipe(acb, tmp_path / "read")
    first = pipe.synthesize(acb.QUERY)
    assert first.query.answer.refused is False
    assert pipe.synthesize(acb.QUERY, no_cache=True).query.cached is False

    pipe = _pipe(acb, tmp_path / "write")
    pipe.synthesize(acb.QUERY, no_cache=True)
    assert pipe.synthesize(acb.QUERY).query.cached is False

    pipe = _pipe(acb, tmp_path / "default")
    pipe.synthesize(acb.QUERY)
    assert pipe.synthesize(acb.QUERY).query.cached is True


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_synthesize_bypass_leaves_the_shock_vector_alone(acb, tmp_path):
    # oracle: spec C3 + Notes (the shock vector is never cached). Relation: a bypassed
    # synthesize and a cached one of the same question give the same shock vector and
    # world_view_id; only query.cached differs (False vs True).
    pipe = _pipe(acb, tmp_path)
    pipe.synthesize(acb.QUERY)
    cached = pipe.synthesize(acb.QUERY)
    fresh = pipe.synthesize(acb.QUERY, no_cache=True)
    assert (cached.query.cached, fresh.query.cached) == (True, False)
    assert set(fresh.shock_vector) == set(cached.shock_vector)
    assert fresh.world_view_id == cached.world_view_id


@pytest.mark.criterion("C3")
@settings(max_examples=20, deadline=None)
@given(flags=st.lists(st.booleans(), min_size=2, max_size=5))
def test_property_synthesize_cached_follows_the_bypass_model(acb, flags):
    # oracle: spec C3 with the C2 model computed in the test (conftest expected_cached):
    # a synthesize's query is cached iff it has no_cache false and an earlier
    # no_cache-false synthesize stored it.
    with tempfile.TemporaryDirectory() as root:
        pipe = _pipe(acb, root)
        results = [pipe.synthesize(acb.QUERY, no_cache=f) for f in flags]
    assert [r.query.cached for r in results] == acb.expected_cached(flags)
