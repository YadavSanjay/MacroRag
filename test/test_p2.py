"""P2 scale & quality: semantic cache, multi-tenancy, time-decay ranking,
LLM-judge eval, drift detection, observability no-op."""

from __future__ import annotations

from datetime import date

from eval.drift import detect_drift, load_baseline, save_baseline
from eval.judge import judge_answer
from llm.fake import FakeLLM
from retriever.ranking import apply_time_decay
from retriever.rerank import IdentityReranker
from retriever.retriever import Retriever
from utils.cache import InMemorySemanticCache, NullCache
from utils.config import RagSettings, RankingSettings, RetrievalSettings
from utils.observability import span
from utils.schema import Answer, AnswerDetail, MetaFilter, RetrievedChunk
from utils.trace import Tracer
from vectorstores.filters import match_payload

# ── L2 semantic cache ─────────────────────────────────────────────────────────

def test_semantic_cache_hit_and_miss():
    sc = InMemorySemanticCache(threshold=0.97)
    sc.add("k1", [1.0, 0.0, 0.0], "value-1")
    assert sc.get([1.0, 0.0, 0.0]) == "value-1"          # identical -> hit
    assert sc.get([0.0, 1.0, 0.0]) is None               # orthogonal -> miss
    assert sc.hits == 1 and sc.misses == 1


def test_semantic_cache_in_pipeline_serves_repeat(indexed, tmp_path):
    from pipelines.query_pipeline import QueryPipeline
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    llm = FakeLLM(responder=lambda s, u: '{"summary":"ans","details":[]}')
    pipe = QueryPipeline(retriever, llm, None, RagSettings(),
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache(),                # L1 off -> exercise L2
                         semantic_cache=InMemorySemanticCache(threshold=0.97))
    pipe.answer("severely adverse unemployment")
    n = len(llm.calls)
    r2 = pipe.answer("severely adverse unemployment")     # identical embedding
    assert r2.cached is True and len(llm.calls) == n      # served from L2


# ── multi-tenancy filter ──────────────────────────────────────────────────────

def test_tenant_filter_sees_own_and_public_only():
    assert match_payload(MetaFilter(tenant_id="t1"), {"tenant_id": "t1"})
    assert match_payload(MetaFilter(tenant_id="t1"), {})            # public
    assert not match_payload(MetaFilter(tenant_id="t1"), {"tenant_id": "t2"})


# ── bucket-aware time-decay ranking ───────────────────────────────────────────

def _rc(cid, score, rel, bucket="B"):
    return RetrievedChunk(cid, cid, 0.0, {"release_date": rel, "bucket": bucket},
                          rerank_score=score)


def test_time_decay_promotes_newer():
    chunks = [_rc("old", 0.90, "2020-01-01"), _rc("new", 0.85, "2026-06-01")]
    out = apply_time_decay(chunks, RankingSettings(
        time_decay=True, half_life_days=365, bucket_decay={"B": 1.0}),
        today=date(2026, 6, 19))
    assert out[0].chunk_id == "new"          # recency beats the small base gap


def test_time_decay_off_is_noop():
    chunks = [_rc("old", 0.90, "2020-01-01"), _rc("new", 0.85, "2026-06-01")]
    out = apply_time_decay(chunks, RankingSettings(time_decay=False))
    assert [c.chunk_id for c in out] == ["old", "new"]


# ── LLM-judge eval ────────────────────────────────────────────────────────────

def test_judge_answer_parses_and_clamps():
    ans = Answer(summary="s", details=[AnswerDetail("d", ["c1"])])
    chunks = [RetrievedChunk("c1", "text", 1.0, {})]
    out = judge_answer(FakeLLM(responses=['{"faithfulness":0.9,"relevancy":0.8}']),
                       "q", ans, chunks)
    assert out["faithfulness"] == 0.9 and out["relevancy"] == 0.8
    clamped = judge_answer(FakeLLM(responses=['{"faithfulness":2,"relevancy":-1}']),
                           "q", ans, chunks)
    assert clamped["faithfulness"] == 1.0 and clamped["relevancy"] == 0.0


# ── drift detection ───────────────────────────────────────────────────────────

def test_detect_drift_flags_regression(tmp_path):
    reg = detect_drift({"hit@5": 0.70, "mrr": 0.50},
                       {"hit@5": 0.80, "mrr": 0.50}, tolerance=0.05)
    assert len(reg) == 1 and reg[0]["metric"] == "hit@5"
    assert detect_drift({"hit@5": 0.79}, {"hit@5": 0.80}, tolerance=0.05) == []
    save_baseline(tmp_path / "b.json", {"hit@5": 0.8})
    assert load_baseline(tmp_path / "b.json")["hit@5"] == 0.8


# ── observability no-op ───────────────────────────────────────────────────────

def test_span_is_noop_when_disabled():
    with span("x", attr=1) as s:        # tracing not initialised -> yields None
        assert s is None
