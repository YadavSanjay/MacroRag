"""In-process MCP server: tool registration + that each tool returns the same
contract the REST endpoints do (fake pipeline; no models/network)."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

pytest.importorskip("mcp")

from llm.fake import FakeLLM  # noqa: E402
from mcp_server.server import build_mcp  # noqa: E402
from pipelines.query_pipeline import QueryPipeline  # noqa: E402
from retriever.rerank import IdentityReranker  # noqa: E402
from retriever.retriever import Retriever  # noqa: E402
from utils.cache import NullCache  # noqa: E402
from utils.config import RagSettings, RetrievalSettings  # noqa: E402
from utils.manifest import build_manifest, write_manifest  # noqa: E402
from utils.trace import Tracer  # noqa: E402

_ANSWER = ('{"summary":"Unemployment peaks at 10%.",'
           '"details":[{"text":"peak 10%","chunk_ids":["dfast_scen::000"]}],'
           '"unanswered":[]}')
_SYNTH = json.dumps({"shock_vector": {
    "USDTWD": {"value": 8, "unit": "pct", "chunk_ids": ["bis_rule::000"]},
    "IG_OAS": {"value": 90, "unit": "bp", "chunk_ids": ["bis_rule::000"]},
}})
_FACTORS = json.dumps({"factors": [
    {"factor_tag": "export_controls", "relevance": "high",
     "entity_tags": ["china"], "chunk_ids": ["bis_rule::000"]},
]})

_TOOLS = {"macro_query", "macro_synthesize_shock_vector", "macro_discover_factors",
          "macro_get_citations", "macro_vocabulary", "macro_shock_schema"}


def _pipe(indexed, tmp_path):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=4))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    # world_view_id comes from the manifest: use a synthetic one, not data/'s
    settings.paths.manifest = str(tmp_path / "manifest.json")
    write_manifest(settings.manifest_path, build_manifest(settings, chunks=len(indexed[2])))

    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return _SYNTH
        if "PORTFOLIO:" in user:
            return _FACTORS
        return _ANSWER

    pipe = QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache())
    return pipe, settings


def _mcp(indexed, tmp_path):
    pipe, settings = _pipe(indexed, tmp_path)
    return build_mcp(lambda: pipe, settings)


def _call(mcp, name, args):
    """Invoke a tool and decode its structured JSON result."""
    blocks = asyncio.run(mcp.call_tool(name, args))
    return json.loads(blocks[0].text)


def test_registers_the_full_tool_surface(indexed, tmp_path):
    mcp = _mcp(indexed, tmp_path)
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == _TOOLS


def test_macro_query_returns_cited_answer(indexed, tmp_path):
    mcp = _mcp(indexed, tmp_path)
    body = _call(mcp, "macro_query",
                 {"question": "severely adverse unemployment stress test"})
    assert "10%" in body["summary"]
    assert any(ch["source_id"] == "dfast" for ch in body["chunks"])


def test_macro_synthesize_returns_gated_vector(indexed, tmp_path):
    mcp = _mcp(indexed, tmp_path)
    body = _call(mcp, "macro_synthesize_shock_vector", {
        "question": "quantify export-control escalation shocks",
        "filters": {"entity_tags": ["china"]}})
    assert set(body["shock_vector"]) == {"USDTWD", "IG_OAS"}
    assert body.get("world_view_id")


def test_macro_synthesize_field_restriction_and_validation(indexed, tmp_path):
    mcp = _mcp(indexed, tmp_path)
    # restrict to a subset
    body = _call(mcp, "macro_synthesize_shock_vector",
                 {"question": "q", "fields": ["IG_OAS"]})
    assert set(body["shock_vector"]) == {"IG_OAS"}
    # unknown field surfaces as a tool error
    with pytest.raises(Exception) as exc:
        _call(mcp, "macro_synthesize_shock_vector",
              {"question": "q", "fields": ["NOPE"]})
    assert "unknown shock field" in str(exc.value)


def test_macro_discover_factors(indexed, tmp_path):
    mcp = _mcp(indexed, tmp_path)
    body = _call(mcp, "macro_discover_factors",
                 {"portfolio": "AI chips exposed to China", "top_k": 5})
    assert [f["factor_tag"] for f in body["factors"]] == ["export_controls"]
    assert body.get("world_view_id")


def test_macro_get_citations_batch(indexed, tmp_path):
    mcp = _mcp(indexed, tmp_path)
    body = _call(mcp, "macro_get_citations",
                 {"chunk_ids": ["bis_rule::000", "bis_rule::000", "missing::999"]})
    assert [c["chunk_id"] for c in body["citations"]] == ["bis_rule::000"]
    assert body["missing"] == ["missing::999"]


def test_macro_vocabulary_and_schema(indexed, tmp_path):
    mcp = _mcp(indexed, tmp_path)
    vocab = _call(mcp, "macro_vocabulary", {})
    assert "export_controls" in vocab["factor_tags"]
    schema = _call(mcp, "macro_shock_schema", {})
    names = {f["name"] for f in schema["fields"]}
    assert {"UST_10Y", "IG_OAS", "USDTWD"} <= names


def test_slow_tool_does_not_freeze_event_loop(indexed, tmp_path):
    """Regression for the stdio deadlock: tool handlers must run their blocking,
    CPU-heavy work in a worker thread, NOT inline on the event loop. Proof:
    several slow calls fired concurrently finish in ~one call's time (parallel
    threads), not N×time (which is what serializing on the loop would give)."""
    import time

    pipe, settings = _pipe(indexed, tmp_path)
    delay = 0.4
    orig = pipe.answer

    def slow_answer(query, *, base_filter=None):
        time.sleep(delay)            # stand-in for model load / CPU rerank / blocking I/O
        return orig(query, base_filter=base_filter)

    pipe.answer = slow_answer
    mcp = build_mcp(lambda: pipe, settings)

    n = 5

    async def fire():
        t0 = time.perf_counter()
        await asyncio.gather(*[mcp.call_tool("macro_query", {"question": f"q{i}"})
                               for i in range(n)])
        return time.perf_counter() - t0

    elapsed = asyncio.run(fire())
    # Serialized-on-the-loop would be >= n*delay (2.0s). Threaded ~= delay (0.4s).
    assert elapsed < delay * n * 0.5, (
        f"calls appear serialized ({elapsed:.2f}s for {n}×{delay}s) — "
        "tool body is blocking the event loop")


def test_warmup_loads_and_exercises_models_in_background(indexed, tmp_path):
    """``warmup=True`` must do more than construct the pipeline — it must run an
    inference so the heavy models load AND their first (cold) torch forward pass
    happens at startup, not on the first real tool call. (Constructing the
    pipeline alone leaves the models lazy, which is what made the first client
    call take minutes.) It must never block server construction."""
    pipe, settings = _pipe(indexed, tmp_path)
    embedded = threading.Event()
    reranked = threading.Event()

    orig_embed = pipe.retriever.embedder.embed_queries
    def spy_embed(texts):
        embedded.set()
        return orig_embed(texts)
    pipe.retriever.embedder.embed_queries = spy_embed

    orig_rerank = pipe.retriever.reranker.rerank
    def spy_rerank(query, chunks, *, top_n=None):
        reranked.set()
        return orig_rerank(query, chunks, top_n=top_n)
    pipe.retriever.reranker.rerank = spy_rerank

    # build_mcp returns immediately; warm() runs on the background warmup thread.
    build_mcp(lambda: pipe, settings, warmup=True)
    assert embedded.wait(timeout=5.0), "warmup never exercised the embedder"
    assert reranked.wait(timeout=5.0), "warmup never exercised the reranker"


def test_principal_stamped_on_the_dormant_hook(indexed, tmp_path):
    # a server pinned to a principal stamps it onto the dormant tenant_id hook
    # (an identity, not a data-isolation boundary — the corpus stays shared)
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=4))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    seen: dict = {}

    class _SpyRetriever:
        embedder = retriever.embedder
        store = retriever.store

        def retrieve(self, query, *, base_filter=None):
            seen["filter"] = base_filter
            return retriever.retrieve(query, base_filter=base_filter)

    pipe = QueryPipeline(_SpyRetriever(), FakeLLM(responder=lambda s, u: _ANSWER),
                         None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache())
    mcp = build_mcp(lambda: pipe, settings, principal="principal-x")
    _call(mcp, "macro_query", {"question": "unemployment"})
    assert seen["filter"] is not None and seen["filter"].tenant_id == "principal-x"


def test_main_preloads_native_modules_before_transport(monkeypatch):
    # Windows deadlock guard: numpy/qdrant_client must be imported on the main
    # thread before build_mcp starts the warmup thread and the stdio reader.
    import sys

    import mcp_server.__main__ as entry

    order: list[str] = []
    real_preload = entry._preload_modules

    def preload():
        real_preload()
        order.append("preload")
        assert "numpy" in sys.modules
        assert "qdrant_client" in sys.modules

    class _Server:
        def run(self, transport):
            order.append(f"run:{transport}")

    def fake_build(principal=None, warmup=True):
        order.append("build")
        return _Server()

    monkeypatch.setattr(entry, "_preload_modules", preload)
    monkeypatch.setattr(entry, "build_mcp", fake_build)
    assert entry.main(["--no-warmup"]) == 0
    assert order == ["preload", "build", "run:stdio"]
