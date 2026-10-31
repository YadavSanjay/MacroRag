"""Fixtures for the answer-usage-cost spec tests.

The cache-scope in-memory pipeline (hashing embedder; in-memory store; identity reranker;
disabled tracer) over a Federal Reserve note and a BIS note, with an injectable L1 cache.
Its LLM is a provider-boundary double (spec Notes): an ``llm.base.LLMClient`` that answers
like the cache-scope responder, logs every call, and reports each one through
``llm.usage.record_call`` with fixed token counts under a configured model id. Expected
costs are computed from that log and the committed price table (``llm.pricing.cost_usd``
over ``configs/llm_pricing.yaml``), never read back from a response. Driven over REST
(FastAPI TestClient, cost caps disabled or enabled with a generous budget) and over MCP
(in process). No network, no ``data/``, no clock.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm.base import LLMClient  # noqa: E402  (needs src on sys.path, set just above)

FED, BIS = "Federal Reserve", "Bank for International Settlements"
QUERY = "how did treasury yields behave"     # no quantitative trigger word: no agent
QUERIES = ("how did treasury yields behave", "what happened to treasury yields",
           "treasury yields across advanced economies", "did markets price cuts")
FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
DOCS = {"fed_note": (FED, "Treasury yields fell as markets priced cuts."),
        "bis_note": (BIS, "Treasury yields fell across advanced economies.")}
PRICED = "gpt-5.4-mini"              # has an entry in configs/llm_pricing.yaml
UNPRICED = "zz-unpriced-model-x"     # no entry and no prefix match there
PROMPT_TOKENS, COMPLETION_TOKENS = 1200, 300
USAGE_KEYS = frozenset({"cost_usd", "cost_complete", "calls", "prompt_tokens",
                        "completion_tokens", "cached_tokens", "total_tokens", "cached"})


def responder(system, user):
    if "SHOCK VECTOR" in user:
        return json.dumps({"shock_vector": {}})
    return json.dumps({"summary": "narrative", "details": [], "unanswered": []})


class PricedLLM(LLMClient):
    """Provider-boundary double: answers like the cache-scope responder and reports each
    call through ``record_call`` with fixed token counts.

    ``model_for(i)`` gives the model id recorded for the i-th call since the last
    ``reset`` (default: always ``model``), so a test can mix priced and unpriced calls.
    """

    def __init__(self, model: str = PRICED, *, prompt_tokens: int = PROMPT_TOKENS,
                 completion_tokens: int = COMPLETION_TOKENS, model_for=None):
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self._model_for = model_for
        self.log: list[dict] = []

    def reset(self) -> None:
        self.log = []

    def complete(self, *, system: str, user: str, temperature: float | None = None,
                 model: str | None = None, purpose: str | None = None) -> str:
        from llm.usage import record_call
        used = self._model_for(len(self.log)) if self._model_for else self.model
        text = responder(system, user)
        self.log.append({"model": used, "purpose": purpose,
                         "prompt_tokens": self.prompt_tokens,
                         "completion_tokens": self.completion_tokens})
        record_call(provider="test", model=used, prompt_tokens=self.prompt_tokens,
                    completion_tokens=self.completion_tokens, purpose=purpose)
        return text


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


def build(root, llm, *, l1=None):
    """A pipeline over the two notes driven by ``llm``; L1 defaults to NullCache, no L2."""
    from embeddings.hashing import HashingEmbedder
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
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest="abcdef012345")
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)
    return QueryPipeline(retriever, llm, None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=l1 if l1 is not None else NullCache(), semantic_cache=None)


def mem_l1():
    from utils.cache import InMemoryCache
    return InMemoryCache()


def rest_client(pipe, *, caps: bool = False):
    """TestClient over ``pipe``; ``caps`` enables the §K cost guard with a generous
    budget (in-memory counters, fixed day so no clock is read)."""
    from fastapi.testclient import TestClient

    from api.app import create_app
    from utils.config import CostSettings
    from utils.costguard import CostGuard, _MemoryStore
    guard = None
    if caps:
        guard = CostGuard(CostSettings(enabled=True, daily_usd=1e6,
                                       per_principal_daily_usd=1e6),
                          _MemoryStore(), today=lambda: "2026-01-02")
    return TestClient(create_app(lambda: pipe, pipe.s, cost_guard=guard))


def post(client, mode: str = "answer", question: str = QUERY) -> dict:
    r = client.post("/query", json={"question": question, "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


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


def mcp_call(mcp, tool: str, args: dict) -> dict:
    blocks = asyncio.run(mcp.call_tool(tool, args))
    return json.loads(blocks[0].text)


def expected_usage(log: list[dict], *, cached: bool) -> dict:
    """Spec Definitions applied to the double's own call log: counts summed from the log,
    cost the sum of ``llm.pricing.cost_usd`` over priced calls, complete iff all priced."""
    from llm.pricing import cost_usd
    costs = [cost_usd(e["model"], prompt_tokens=e["prompt_tokens"],
                      completion_tokens=e["completion_tokens"]) for e in log]
    prompt = sum(e["prompt_tokens"] for e in log)
    completion = sum(e["completion_tokens"] for e in log)
    return {"cost_usd": float(sum(c for c in costs if c is not None)),
            "cost_complete": all(c is not None for c in costs),
            "calls": len(log), "prompt_tokens": prompt, "completion_tokens": completion,
            "cached_tokens": 0, "total_tokens": prompt + completion, "cached": cached}


def assert_usage(body: dict, expected: dict, *, rel: float = 1e-9) -> None:
    """``body`` carries a top-level usage block equal to ``expected``: floats to
    ``rel`` (summation order may differ), everything else exact."""
    assert isinstance(body, dict), body
    assert "usage" in body, f"response has no usage block: keys {sorted(body)}"
    usage = body["usage"]
    assert isinstance(usage, dict), usage
    assert set(usage) == USAGE_KEYS, sorted(usage)
    assert usage["cost_usd"] == pytest.approx(expected["cost_usd"], rel=rel, abs=0.0)
    rest = {k: v for k, v in expected.items() if k != "cost_usd"}
    assert {k: usage[k] for k in rest} == rest


def check_tables() -> None:
    """The ids the spec relies on: PRICED has a price, UNPRICED has none."""
    from llm.pricing import model_price
    assert model_price(PRICED) is not None
    assert model_price(UNPRICED) is None


@pytest.fixture(scope="session")
def auc():
    """Namespace of the helpers above, for the test modules."""
    check_tables()
    return types.SimpleNamespace(
        QUERY=QUERY, QUERIES=QUERIES, PRICED=PRICED, UNPRICED=UNPRICED,
        PROMPT_TOKENS=PROMPT_TOKENS, COMPLETION_TOKENS=COMPLETION_TOKENS,
        USAGE_KEYS=USAGE_KEYS, PricedLLM=PricedLLM, build=build, mem_l1=mem_l1,
        rest_client=rest_client, post=post, sse_events=sse_events,
        mcp_server=mcp_server, mcp_call=mcp_call, expected_usage=expected_usage,
        assert_usage=assert_usage)
