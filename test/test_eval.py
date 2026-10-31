"""Eval metrics + retrieval harness."""

from __future__ import annotations

from eval import metrics
from eval.harness import evaluate_retrieval
from retriever.rerank import IdentityReranker
from retriever.retriever import Retriever
from utils.config import RetrievalSettings


def test_hit_recall_mrr():
    retrieved = ["a", "b", "c", "d"]
    assert metrics.hit_at_k(retrieved, ["c"], 3) == 1.0
    assert metrics.hit_at_k(retrieved, ["z"], 3) == 0.0
    assert metrics.recall_at_k(retrieved, ["a", "c"], 4) == 1.0
    assert metrics.recall_at_k(retrieved, ["a", "z"], 4) == 0.5
    assert metrics.reciprocal_rank(retrieved, ["b"]) == 0.5


def test_hit_recall_mrr_list_valued_fields():
    # factor_tags / entity_tags are list-valued payloads: each retrieved element
    # is a list of tags. A chunk hits when its tag-set intersects gold.
    retrieved = [["fiscal", "growth"], ["term_premium", "rates_vol"], ["fx"]]
    assert metrics.hit_at_k(retrieved, ["term_premium"], 3) == 1.0
    assert metrics.hit_at_k(retrieved, ["credit_spread"], 3) == 0.0
    # both gold tags appear across the top-3 (in different chunks) -> full recall
    assert metrics.recall_at_k(retrieved, ["fiscal", "fx"], 3) == 1.0
    assert metrics.recall_at_k(retrieved, ["fiscal", "credit_spread"], 3) == 0.5
    assert metrics.reciprocal_rank(retrieved, ["term_premium"]) == 0.5
    # a missing field (None) must never match
    assert metrics.hit_at_k([None, ["fx"]], ["fx"], 2) == 1.0
    assert metrics.hit_at_k([None], ["fx"], 1) == 0.0


def test_aggregate_means():
    agg = metrics.aggregate([{"m": 1.0}, {"m": 0.0}])
    assert agg["m"] == 0.5


def test_evaluate_retrieval_over_synthetic_corpus(indexed):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=5))
    golden = [
        {"question": "severely adverse unemployment stress test scenario",
         "match_field": "source_id", "expected": ["dfast"]},
        {"question": "export controls semiconductors China",
         "match_field": "source_id", "expected": ["federal_register"]},
    ]
    report = evaluate_retrieval(retriever, golden, k=5)
    assert report["summary"]["hit@5"] == 1.0
    assert report["summary"]["mrr"] > 0
