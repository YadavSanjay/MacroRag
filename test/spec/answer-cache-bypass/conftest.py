"""Fixtures for the answer-cache-bypass spec tests.

The cache-scope in-memory pipeline (hashing embedder, so identical text embeds identically;
in-memory store; identity reranker; scripted FakeLLM; disabled tracer) over a Federal Reserve
note and a BIS note, with injectable L1 and L2 caches and an optional relevance floor that
forces a refusal. Driven directly, over REST (FastAPI TestClient) and over MCP (in process).
No network, no ``data/``, no clock.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

FED, BIS = "Federal Reserve", "Bank for International Settlements"
QUERY = "how did treasury yields behave"     # no quantitative trigger word: no agent
# Questions for the property tests, none with a quantitative trigger word.
QUERIES = ("how did treasury yields behave", "what happened to treasury yields",
           "treasury yields across advanced economies", "did markets price cuts")
# Above every chunk score the fixture can produce: every answer is a refusal.
REFUSE_FLOOR = 1e9
FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
DOCS = {"fed_note": (FED, "Treasury yields fell as markets priced cuts."),
        "bis_note": (BIS, "Treasury yields fell across advanced economies.")}
MCP_TOOLS = ("macro_query", "macro_synthesize_shock_vector")


def _doc(doc_id: str) -> dict:
    publisher, text = DOCS[doc_id]
    meta = {
        "publisher": publisher, "publication": doc_id, "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": f"https://example.invalid/{doc_id}",
        "content_hash": doc_id, "licence": "public_domain_us_gov", "tier": 3, "bucket": "C",
        "doc_type": "research_note", "section": None, "entity_tags": [],
        "factor_tags": ["cross_asset_scenario"], "trades_affected": [],
    }
    return {
        "doc_id": doc_id, "source_id": doc_id, "title": doc_id, "doc_type": "research_note",
        "publisher": publisher, "publication": doc_id, "release_date": "2026-02-05",
        "retrieval_date": "2026-06-19", "url": meta["url"], "content_hash": doc_id,
        "tier": 3, "bucket": "C",
        "chunks": [{"chunk_id": f"{doc_id}::000", "seq": 0, "text": text, "metadata": meta}],
        "timeseries": [],
    }


def responder(system, user):
    if "SHOCK VECTOR" in user:
        return json.dumps({"shock_vector": {}})
    return json.dumps({"summary": "narrative", "details": [], "unanswered": []})


def build(root, *, l1=None, l2=None, floor: float | None = None):
    """A pipeline over the two notes; L1 defaults to NullCache, L2 to none.

    ``floor`` sets ``retrieval.min_rerank_score`` (``REFUSE_FLOOR`` forces a refusal).
    """
    from embeddings.hashing import HashingEmbedder
    from llm.fake import FakeLLM
    from pipelines.query_pipeline import QueryPipeline
    from retriever.rerank import IdentityReranker
    from retriever.retriever import Retriever
    from utils.cache import NullCache
    from utils.config import RagSettings, RetrievalSettings
    from utils.corpus import iter_chunk_points
    from utils.manifest import build_manifest, write_manifest
    from utils.trace import Tracer
    from vectorstores.memory import InMemoryVectorStore

    root = Path(root)
    corpus = root / "processed"
    (corpus / "tierx").mkdir(parents=True, exist_ok=True)
    for doc_id in DOCS:
        (corpus / "tierx" / f"{doc_id}.json").write_text(json.dumps(_doc(doc_id)),
                                                         encoding="utf-8")
    embedder = HashingEmbedder(dense_dim=64)
    store = InMemoryVectorStore()
    points = list(iter_chunk_points(corpus))
    store.upsert(points, embedder.embed_documents([p.embed_text for p in points]))
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=10))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    settings.retrieval.hydrate_timeseries = False
    settings.escalation.enabled = False
    settings.semantic_cache.enabled = False
    if floor is not None:
        settings.retrieval.min_rerank_score = floor
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest="abcdef012345")
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)
    return QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=l1 if l1 is not None else NullCache(), semantic_cache=l2)


def mem_l1():
    from utils.cache import InMemoryCache
    return InMemoryCache()


def mem_l2():
    from utils.cache import InMemorySemanticCache
    return InMemorySemanticCache(threshold=0.97)


def caches(levels: str):
    """(l1, l2) for ``"l1"``, ``"l2"`` or ``"both"`` in-memory levels."""
    return (mem_l1() if levels in ("l1", "both") else None,
            mem_l2() if levels in ("l2", "both") else None)


def assert_keyword(func, name: str = "no_cache") -> None:
    """``func`` takes keyword-only ``name`` defaulting to False (spec Interface)."""
    params = inspect.signature(func).parameters
    assert name in params, f"{func.__qualname__} has no {name!r} keyword"
    assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[name].default is False


def assert_request_field() -> None:
    from api.models import QueryRequest
    assert "no_cache" in QueryRequest.model_fields, "QueryRequest has no no_cache field"
    field = QueryRequest.model_fields["no_cache"]
    assert field.annotation is bool
    assert field.default is False


def expected_cached(flags) -> list[bool]:
    """Spec C2 model of the caches over one question: a call is cached iff it reads the
    cache (no_cache false) and an earlier call wrote it (an earlier no_cache-false call)."""
    out, warm = [], False
    for no_cache in flags:
        out.append((not no_cache) and warm)
        warm = warm or not no_cache
    return out


def rest_client(pipe):
    from fastapi.testclient import TestClient

    from api.app import create_app
    return TestClient(create_app(lambda: pipe, pipe.s))


def sse_events(text: str) -> dict[str, dict]:
    """Parse a text/event-stream body into {event name: data dict}."""
    out: dict[str, dict] = {}
    for block in text.split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if name is not None:
            out[name] = data or {}
    return out


def mcp_server(pipe):
    from mcp_server.server import build_mcp
    return build_mcp(lambda: pipe, pipe.s)


def mcp_schemas(mcp) -> dict[str, dict]:
    """{tool name: input JSON schema} as the MCP server lists them."""
    return {t.name: t.inputSchema for t in asyncio.run(mcp.list_tools())}


def assert_mcp_no_cache(mcp, tool: str) -> None:
    schema = mcp_schemas(mcp)[tool]
    props = schema.get("properties", {})
    assert "no_cache" in props, f"{tool} input schema has no no_cache"
    assert props["no_cache"].get("type") == "boolean"
    assert props["no_cache"].get("default") is False
    assert "no_cache" not in schema.get("required", [])


def mcp_call(mcp, tool: str, args: dict) -> dict:
    blocks = asyncio.run(mcp.call_tool(tool, args))
    return json.loads(blocks[0].text)


@pytest.fixture(scope="session")
def acb():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        QUERY=QUERY, QUERIES=QUERIES, REFUSE_FLOOR=REFUSE_FLOOR, MCP_TOOLS=MCP_TOOLS,
        build=build, mem_l1=mem_l1, mem_l2=mem_l2, caches=caches,
        assert_keyword=assert_keyword, assert_request_field=assert_request_field,
        expected_cached=expected_cached, rest_client=rest_client, sse_events=sse_events,
        mcp_server=mcp_server, mcp_schemas=mcp_schemas,
        assert_mcp_no_cache=assert_mcp_no_cache, mcp_call=mcp_call)
