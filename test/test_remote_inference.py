"""Remote inference clients: the RagInference-backed embedder + reranker that let
MacroRAG share one copy of bge-m3 / bge-reranker with ARag instead of loading
its own. All HTTP is mocked — no service, no models, no network."""

from __future__ import annotations

import httpx
import pytest

from embeddings.remote_embedder import RemoteEmbedder
from retriever.remote_reranker import RemoteReranker, remote_inference_enabled, resolve_endpoint
from utils.schema import RetrievedChunk

_URL = "http://raginfer.local:8799"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _capture(payload):
    """Return (post_fn, sent) where sent records the last request kwargs."""
    sent: dict = {}

    def fake_post(url, json=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        sent["timeout"] = timeout
        return _FakeResp(payload)

    return fake_post, sent


@pytest.fixture
def raginfer_env(monkeypatch):
    monkeypatch.setenv("RAGINFER_URL", _URL)
    monkeypatch.delenv("RAGINFER_FALLBACK_LOCAL", raising=False)
    monkeypatch.delenv("RAGINFER_RERANK_MODEL", raising=False)
    monkeypatch.delenv("RAGINFER_EMBED_MODEL", raising=False)


# ── config / endpoint resolution ─────────────────────────────────────────────

def test_remote_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("RAGINFER_URL", raising=False)
    assert remote_inference_enabled() is False
    assert resolve_endpoint("/rerank") == ""


def test_endpoint_resolution(raginfer_env):
    assert remote_inference_enabled() is True
    assert resolve_endpoint("/rerank") == f"{_URL}/rerank"
    assert resolve_endpoint("/embed", explicit="http://x/y") == "http://x/y"


def test_clients_require_a_url(monkeypatch):
    monkeypatch.delenv("RAGINFER_URL", raising=False)
    with pytest.raises(ValueError):
        RemoteReranker()
    with pytest.raises(ValueError):
        RemoteEmbedder()


# ── reranker ─────────────────────────────────────────────────────────────────

def _chunks(*texts):
    return [RetrievedChunk(chunk_id=f"c{i}", text=t, score=0.0, payload={})
            for i, t in enumerate(texts)]


def test_reranker_scores_sort_and_truncate(raginfer_env, monkeypatch):
    post, sent = _capture({"scores": [0.1, 0.9, 0.5], "model": "bge", "count": 3})
    monkeypatch.setattr(httpx, "post", post)

    rr = RemoteReranker()
    out = rr.rerank("the query", _chunks("a", "b", "c"), top_n=2)

    # service-aligned scores assigned, sorted desc, truncated to top_n
    assert [c.chunk_id for c in out] == ["c1", "c2"]
    assert out[0].rerank_score == pytest.approx(0.9)
    assert out[1].rerank_score == pytest.approx(0.5)
    # wire contract: query + documents in input order
    assert sent["json"]["query"] == "the query"
    assert sent["json"]["documents"] == ["a", "b", "c"]
    assert sent["url"] == f"{_URL}/rerank"


def test_reranker_empty_is_noop(raginfer_env, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    assert RemoteReranker().rerank("q", []) == []
    assert called["n"] == 0          # no HTTP for an empty batch


def test_reranker_count_mismatch_raises(raginfer_env, monkeypatch):
    post, _ = _capture({"scores": [0.1], "model": "bge", "count": 1})
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ValueError, match="2 docs"):
        RemoteReranker().rerank("q", _chunks("a", "b"))


def test_reranker_model_override_is_sent(monkeypatch):
    monkeypatch.setenv("RAGINFER_URL", _URL)
    monkeypatch.setenv("RAGINFER_RERANK_MODEL", "bge")
    post, sent = _capture({"scores": [0.5], "model": "bge", "count": 1})
    monkeypatch.setattr(httpx, "post", post)
    RemoteReranker().rerank("q", _chunks("a"))
    assert sent["json"]["model"] == "bge"


# ── embedder ─────────────────────────────────────────────────────────────────

def test_embedder_rebuilds_dense_and_sparse(raginfer_env, monkeypatch):
    payload = {"embeddings": [
        {"dense": [0.1, 0.2, 0.3],
         "sparse": {"indices": [5, 9], "values": [0.7, 0.3]}},
        {"dense": [0.4, 0.5, 0.6],
         "sparse": {"indices": [1], "values": [1.0]}},
    ], "model": "BAAI/bge-m3", "count": 2}
    post, sent = _capture(payload)
    monkeypatch.setattr(httpx, "post", post)

    embs = RemoteEmbedder().embed_queries(["hello", "world"])
    assert len(embs) == 2
    assert embs[0].dense == [0.1, 0.2, 0.3]
    assert embs[0].sparse.indices == [5, 9]
    assert embs[0].sparse.values == [0.7, 0.3]
    assert embs[1].sparse.indices == [1]
    # wire contract: dense+sparse both requested, texts in order
    assert sent["json"] == {"texts": ["hello", "world"], "dense": True, "sparse": True}
    assert sent["url"] == f"{_URL}/embed"


def test_embedder_empty_and_count_mismatch(raginfer_env, monkeypatch):
    assert RemoteEmbedder().embed_documents([]) == []
    post, _ = _capture({"embeddings": [{"dense": [1.0]}], "model": "m", "count": 1})
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ValueError, match="2 texts"):
        RemoteEmbedder().embed_queries(["a", "b"])


# ── wiring: build_retriever routes to the service when RAGINFER_URL is set ────

def _memory_settings():
    from utils.config import RagSettings
    s = RagSettings()
    s.qdrant.url = None
    s.qdrant.location = ":memory:"   # avoid the embedded on-disk lock in tests
    s.qdrant.path = None
    return s


def test_build_retriever_uses_remote_clients(raginfer_env):
    from pipelines.query_pipeline import build_retriever

    r = build_retriever(_memory_settings())   # llm=None -> heuristic analyzer, no LLM
    assert isinstance(r.embedder, RemoteEmbedder)
    assert isinstance(r.reranker, RemoteReranker)


def test_build_retriever_defaults_to_remote_service(monkeypatch):
    """Project default: retrieval uses the shared inference service even when
    RAGINFER_URL is unset — driven by configs/rag.yaml `inference.remote_url`.
    (Remote clients don't load models, so this stays offline/no-torch.)"""
    monkeypatch.delenv("RAGINFER_URL", raising=False)
    from pipelines.query_pipeline import build_retriever

    s = _memory_settings()
    assert s.inference.remote_url                       # committed default present
    r = build_retriever(s)
    assert isinstance(r.embedder, RemoteEmbedder)
    assert isinstance(r.reranker, RemoteReranker)
    # the clients resolved the config base URL into per-endpoint targets
    assert r.embedder.url.endswith("/embed")
    assert r.reranker.url.endswith("/rerank")


# ── wiring: the indexing flow embeds via the same remote selection ───────────

def test_select_embedder_uses_remote_when_configured(monkeypatch):
    """Ingestion/index-time embedding routes to RagInference (parity with query)
    whenever a service URL resolves — never loads bge-m3/torch in-process."""
    monkeypatch.delenv("RAGINFER_URL", raising=False)   # rely on the config default
    from embeddings import select_embedder

    s = _memory_settings()
    assert s.inference.remote_url                        # committed default present
    emb = select_embedder(s)
    assert isinstance(emb, RemoteEmbedder)
    assert emb.url.endswith("/embed")
    # config fallback_local (default True) flows through to the client
    assert emb._fallback is s.inference.fallback_local


def test_select_embedder_local_when_disabled(monkeypatch):
    """An empty RAGINFER_URL forces in-process models: select_embedder must fall
    back to build_embedder rather than a RemoteEmbedder. Patched so the test never
    imports torch/FlagEmbedding."""
    monkeypatch.setenv("RAGINFER_URL", "")              # present but empty -> disable
    import embeddings
    sentinel = object()
    monkeypatch.setattr(embeddings, "build_embedder", lambda settings: sentinel)

    assert embeddings.select_embedder(_memory_settings()) is sentinel


def test_resolve_inference_url_precedence(monkeypatch):
    """URL resolution: env override > committed config default; empty env disables.
    (Pure decision — no models/network, so all three cases are testable.)"""
    from pipelines.query_pipeline import resolve_inference_url
    s = _memory_settings()
    s.inference.remote_url = "http://config-default:8799"

    monkeypatch.delenv("RAGINFER_URL", raising=False)
    assert resolve_inference_url(s) == "http://config-default:8799"   # config default

    monkeypatch.setenv("RAGINFER_URL", "http://override:9000/")
    assert resolve_inference_url(s) == "http://override:9000"          # env overrides

    monkeypatch.setenv("RAGINFER_URL", "")             # present but empty -> disable
    assert resolve_inference_url(s) == ""              # forces in-process models
