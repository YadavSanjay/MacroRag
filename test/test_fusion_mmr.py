"""RRF fusion, dedup, and MMR selection (pure functions)."""

from __future__ import annotations

from retriever.fusion import dedup_by_chunk, reciprocal_rank_fusion
from retriever.mmr import mmr_select
from utils.schema import RetrievedChunk


def _c(cid, score=1.0):
    return RetrievedChunk(chunk_id=cid, text=cid, score=score, payload={})


def test_rrf_rewards_agreement_across_branches():
    b1 = [_c("a"), _c("b"), _c("c")]
    b2 = [_c("b"), _c("a"), _c("d")]
    fused = reciprocal_rank_fusion([b1, b2])
    # 'b' is rank0+rank1, 'a' is rank0+rank1 too -> both top; appears once each
    ids = [c.chunk_id for c in fused]
    assert set(ids[:2]) == {"a", "b"}
    assert ids.count("a") == 1 and len(ids) == 4


def test_rrf_single_branch_preserves_order():
    fused = reciprocal_rank_fusion([[_c("a"), _c("b"), _c("c")]])
    assert [c.chunk_id for c in fused] == ["a", "b", "c"]


def test_dedup_keeps_highest_score():
    out = dedup_by_chunk([_c("a", 0.2), _c("a", 0.9), _c("b", 0.5)])
    by = {c.chunk_id: c.score for c in out}
    assert by["a"] == 0.9 and by["b"] == 0.5


def test_mmr_identity_when_lambda_one():
    cands = [("a", [1, 0]), ("b", [0, 1]), ("c", [1, 1])]
    assert mmr_select([1, 0], cands, k=2, lambda_=1.0) == ["a", "b"]


def test_mmr_diversifies_away_from_near_duplicate():
    # Query sits between the axes. 'a2' is most relevant and 'a' is nearly
    # identical to it; with diversity the second pick should be the different
    # vector 'b', and the near-duplicate 'a' should be dropped.
    cands = [("a", [1.0, 0.0]), ("a2", [0.99, 0.14]), ("b", [0.0, 1.0])]
    order = mmr_select([1.0, 1.0], cands, k=2, lambda_=0.5)
    assert order == ["a2", "b"]
