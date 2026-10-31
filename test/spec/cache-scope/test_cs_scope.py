"""C1-C4: cached answers stay within their filter, retrieval options and corpus."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _filter_then_other(cs, l2, root):
    pipe = cs.build(root, l2=l2)
    first = pipe.answer(cs.QUERY, base_filter=cs.pubs_filter([cs.FED]))
    second = pipe.answer(cs.QUERY, base_filter=cs.pubs_filter([cs.BIS]))
    again = pipe.answer(cs.QUERY, base_filter=cs.pubs_filter([cs.BIS]))
    return first, second, again


@pytest.mark.criterion("C1")
def test_semantic_hit_only_within_the_same_filter(cs, tmp_path):
    # oracle: spec C1 — Fed-filtered then BIS-filtered: the BIS call is not served from
    # the Fed entry (not cached, BIS chunks only); a repeat BIS call is cached.
    first, second, again = _filter_then_other(cs, cs.mem_l2(), tmp_path)
    assert cs.publishers(first) == {cs.FED}
    assert second.cached is False
    assert cs.publishers(second) == {cs.BIS}
    assert again.cached is True and cs.publishers(again) == {cs.BIS}


@pytest.mark.criterion("C1")
@settings(max_examples=6, deadline=None)
@given(order=st.permutations(["Federal Reserve", "Bank for International Settlements"]))
def test_property_each_filter_gets_its_own_publishers(cs, order):
    # oracle: spec C1 computed in the test: whichever filter comes first, every answer's
    # chunks are from its own filter's publisher.
    with tempfile.TemporaryDirectory() as root:
        pipe = cs.build(root, l2=cs.mem_l2())
        results = [pipe.answer(cs.QUERY, base_filter=cs.pubs_filter([p])) for p in order]
    assert [cs.publishers(r) for r in results] == [{p} for p in order]


@pytest.mark.criterion("C2")
def test_synthesize_does_not_reuse_a_plain_answer(cs, tmp_path):
    # oracle: spec C2 — a plain answer, then synthesize on the same question: synthesize
    # retrieves with its own options, so its query result is not a cache hit.
    pipe = cs.build(tmp_path, l2=cs.mem_l2())
    pipe.answer(cs.QUERY)
    syn = pipe.synthesize(cs.QUERY)
    assert syn.query.cached is False


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_synthesize_twice_still_hits(cs, tmp_path):
    # oracle: spec C2. Relation: synthesize after a plain answer is a miss, while a second
    # synthesize of the same question is a hit (the cache still works within its scope).
    pipe = cs.build(tmp_path, l2=cs.mem_l2())
    pipe.answer(cs.QUERY)
    first = pipe.synthesize(cs.QUERY)
    second = pipe.synthesize(cs.QUERY)
    assert first.query.cached is False
    assert second.query.cached is True


@pytest.mark.criterion("C3")
def test_corpus_change_invalidates_both_levels(cs):
    # oracle: spec C3 — two pipelines share one L1 and one L2; their manifests differ only
    # in the corpus digest, so the second's answer is not cached (each level checked alone).
    for l1, l2 in ((cs.mem_l1(), None), (None, cs.mem_l2())):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            cs.build(a, l1=l1, l2=l2, digest="aaaaaaaaaaaa").answer(cs.QUERY)
            res = cs.build(b, l1=l1, l2=l2, digest="bbbbbbbbbbbb").answer(cs.QUERY)
        assert res.cached is False, ("L1" if l1 is not None else "L2")


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_same_corpus_shares_the_cache(cs):
    # oracle: spec C3. Relation: with the same digest the second pipeline is served from
    # the shared cache; with a different digest it is not.
    l1 = cs.mem_l1()
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b, \
            tempfile.TemporaryDirectory() as c:
        cs.build(a, l1=l1, digest="aaaaaaaaaaaa").answer(cs.QUERY)
        same = cs.build(b, l1=l1, digest="aaaaaaaaaaaa").answer(cs.QUERY)
        other = cs.build(c, l1=l1, digest="cccccccccccc").answer(cs.QUERY)
    assert same.cached is True
    assert other.cached is False


@pytest.mark.criterion("C4")
def test_redis_semantic_cache_is_scoped_too(cs, tmp_path):
    # oracle: spec C4 — the Redis-backed L2 behaves as C1.
    first, second, again = _filter_then_other(cs, cs.redis_l2(), tmp_path)
    assert cs.publishers(first) == {cs.FED}
    assert second.cached is False and cs.publishers(second) == {cs.BIS}
    assert again.cached is True


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_redis_and_memory_agree(cs):
    # oracle: spec C4. Relation: the same call sequence gives the same cached flags and
    # publishers with either backend.
    out = []
    for l2 in (cs.mem_l2(), cs.redis_l2()):
        with tempfile.TemporaryDirectory() as root:
            out.append([(r.cached, sorted(cs.publishers(r)))
                        for r in _filter_then_other(cs, l2, root)])
    assert out[0] == out[1]
    assert [cached for cached, _ in out[0]] == [False, False, True]
