"""P1 serving & cost: L1 cache, tiered LLM routing, index manifest."""

from __future__ import annotations

from llm.fake import FakeLLM
from pipelines.query_pipeline import QueryPipeline
from retriever.rerank import IdentityReranker
from retriever.retriever import Retriever
from utils.cache import InMemoryCache, NullCache, cache_key, normalize_query
from utils.config import RagSettings, RetrievalSettings
from utils.schema import MetaFilter, QueryAnalysis
from utils.trace import Tracer

# ── P1.6 cache ───────────────────────────────────────────────────────────────

def test_normalize_query_and_key_stability():
    assert normalize_query("  Fed   FUNDS rate ") == "fed funds rate"
    k1 = cache_key("Fed funds rate", MetaFilter(factor_tags=["a", "b"]), version="v1")
    k2 = cache_key("fed  funds   rate", MetaFilter(factor_tags=["b", "a"]), version="v1")
    assert k1 == k2                                   # case/space/tag-order invariant
    assert k1 != cache_key("Fed funds rate", None, version="v2")  # version busts


def test_inmemory_cache_ttl_and_lru():
    c = InMemoryCache(ttl_seconds=0, max_entries=2)   # ttl=0 -> never expire
    c.set("a", 1); c.set("b", 2)
    assert c.get("a") == 1
    c.set("c", 3)                                     # evicts LRU ("b")
    assert c.get("b") is None and c.get("c") == 3
    assert c.hits >= 1 and c.misses >= 1


def _pipeline(indexed, tmp_path, llm, *, cache=None, settings=None):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    return QueryPipeline(retriever, llm, None, settings or RagSettings(),
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=cache)


def test_pipeline_cache_hit_skips_llm(indexed, tmp_path):
    llm = FakeLLM(responses=['{"summary":"first","details":[],"unanswered":[]}'])
    pipe = _pipeline(indexed, tmp_path, llm, cache=InMemoryCache(ttl_seconds=0))
    r1 = pipe.answer("severely adverse unemployment stress test")
    assert r1.cached is False and len(llm.calls) >= 1
    before = len(llm.calls)
    r2 = pipe.answer("severely adverse unemployment stress test")
    assert r2.cached is True
    assert len(llm.calls) == before                  # no new LLM calls on hit
    assert r2.answer.summary == r1.answer.summary


def test_null_cache_always_misses(indexed, tmp_path):
    llm = FakeLLM(responder=lambda s, u: '{"summary":"x","details":[]}')
    pipe = _pipeline(indexed, tmp_path, llm, cache=NullCache())
    a = pipe.answer("unemployment")
    b = pipe.answer("unemployment")
    assert a.cached is False and b.cached is False


# ── P1.7 tiered routing ──────────────────────────────────────────────────────

def test_tiered_routing_picks_model_by_route(indexed, tmp_path):
    settings = RagSettings()
    settings.llm.answer_model = "cheap-model"
    settings.llm.complex_model = "frontier-model"
    settings.llm.plan_model = "plan-model"
    settings.retrieval.verify_faithfulness = False

    # DIRECT route -> cheap generation model
    llm = FakeLLM(responses=['{"summary":"s","details":[]}'])
    pipe = _pipeline(indexed, tmp_path, llm, settings=settings)
    pipe.answer("severely adverse unemployment stress test")
    assert "cheap-model" in llm.models_used
    assert "frontier-model" not in llm.models_used


def test_tiered_routing_uses_frontier_for_complex(indexed, tmp_path):
    settings = RagSettings()
    settings.llm.answer_model = "cheap-model"
    settings.llm.complex_model = "frontier-model"
    settings.retrieval.verify_faithfulness = False

    class _Decompose:
        def analyze(self, q, *, base_filter=None):
            return QueryAnalysis(original=q, route="DECOMPOSE", filters=MetaFilter())

    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(), _Decompose(),
                          RetrievalSettings(final_k=2))
    llm = FakeLLM(responses=['{"summary":"s","details":[]}'])
    pipe = QueryPipeline(retriever, llm, None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache())
    pipe.answer("a complex multi-part question")
    assert "frontier-model" in llm.models_used


# ── P1.8 manifest ────────────────────────────────────────────────────────────

def test_manifest_build_write_read_and_staleness(tmp_path):
    from utils import manifest as M
    settings = RagSettings()
    settings.paths.manifest = str(tmp_path / "manifest.json")

    assert M.is_stale(settings) is True               # nothing written yet
    man = M.build_manifest(settings, chunks=10, observations=5)
    M.write_manifest(settings.manifest_path, man)
    assert M.read_manifest(settings.manifest_path)["chunks"] == 10
    assert M.is_stale(settings) is False              # matches current settings

    settings.embedding.model = "different-embedder"   # embedding drift
    assert M.is_stale(settings) is True
