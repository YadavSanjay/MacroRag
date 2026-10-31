"""C2: QueryPipeline.answer(no_cache=True) neither reads nor writes either cache."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _pipe(acb, root):
    from pipelines.query_pipeline import QueryPipeline
    acb.assert_keyword(QueryPipeline.answer)
    return acb.build(root, l1=acb.mem_l1(), l2=acb.mem_l2())


@pytest.mark.criterion("C2")
def test_no_cache_does_not_read(acb, tmp_path):
    # oracle: spec C2 "not read" — answer(q) then answer(q, no_cache=True): the second
    # is not cached, though both caches hold q.
    pipe = _pipe(acb, tmp_path)
    first = pipe.answer(acb.QUERY)
    assert first.answer.refused is False
    second = pipe.answer(acb.QUERY, no_cache=True)
    assert second.cached is False
    assert second.answer.refused is False


@pytest.mark.criterion("C2")
def test_no_cache_does_not_write(acb, tmp_path):
    # oracle: spec C2 "not written" — answer(q, no_cache=True) then answer(q): the second
    # is not cached (nothing was stored), and a third answer(q) is cached.
    pipe = _pipe(acb, tmp_path)
    pipe.answer(acb.QUERY, no_cache=True)
    second = pipe.answer(acb.QUERY)
    third = pipe.answer(acb.QUERY)
    assert second.cached is False
    assert third.cached is True


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_no_cache_false_is_the_same_as_omitting_it(acb, tmp_path):
    # oracle: spec C2 "no_cache=False behaves exactly as omitting it". Relation: the
    # sequence omit, False, False on one pipeline gives the same cached flags and answer
    # as omit, omit, omit on another: [False, True, True].
    with_flag = _pipe(acb, tmp_path / "flag")
    omitted = acb.build(tmp_path / "omit", l1=acb.mem_l1(), l2=acb.mem_l2())
    a = [with_flag.answer(acb.QUERY), with_flag.answer(acb.QUERY, no_cache=False),
         with_flag.answer(acb.QUERY, no_cache=False)]
    b = [omitted.answer(acb.QUERY) for _ in range(3)]
    assert [r.cached for r in a] == [r.cached for r in b] == [False, True, True]
    assert [r.answer.summary for r in a] == [r.answer.summary for r in b]


@pytest.mark.criterion("C2")
@settings(max_examples=25, deadline=None)
@given(levels=st.sampled_from(["l1", "l2", "both"]),
       flags=st.lists(st.booleans(), min_size=2, max_size=6))
def test_property_cached_follows_the_bypass_model(acb, levels, flags):
    # oracle: spec C2 computed in the test (conftest expected_cached): a call is cached
    # iff it has no_cache false and an earlier no_cache-false call stored the answer;
    # this holds with L1 alone, L2 alone, or both.
    from pipelines.query_pipeline import QueryPipeline
    acb.assert_keyword(QueryPipeline.answer)
    l1, l2 = acb.caches(levels)
    with tempfile.TemporaryDirectory() as root:
        pipe = acb.build(root, l1=l1, l2=l2)
        results = [pipe.answer(acb.QUERY, no_cache=f) for f in flags]
    assert [r.cached for r in results] == acb.expected_cached(flags)
    assert not any(r.answer.refused for r in results)
