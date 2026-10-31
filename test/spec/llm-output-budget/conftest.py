"""Fixtures for the llm-output-budget spec tests.

Puts the repo ``src`` on ``sys.path``, turns the repo dotenv file off before anything imports
``utils.config``, unsets ``MACRORAG_USE_REPLACEMENT_MODELS`` for every test, clears the
``load_settings`` cache and the process-global schema-health aggregate around each test.

Fakes (spec Notes) sit at the provider boundary only:

- ``openai`` / ``anthropic`` SDK modules placed at ``sys.modules`` (``install_fake``), which
  record each create call's kwargs and return namespaces shaped like the SDK responses;
- ``llm.base.LLMClient`` test doubles standing in for the provider (``TierRecorder`` for the
  in-memory pipeline of C7/C8, built like the cache-scope / answer-usage-cost conftests).

Any YAML a test loads is written under a temporary directory. No network, no ``data/``, no
clock.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

# Before any import of utils.config: never load the repo's secrets dotenv file.
os.environ["MACRORAG_DOTENV"] = "0"

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm.base import LLMClient  # noqa: E402  (needs src on sys.path, set just above)

ENV_VAR = "MACRORAG_USE_REPLACEMENT_MODELS"
RAG_YAML = _ROOT / "configs" / "rag.yaml"

# spec Notes: the effort sets.
ANTHROPIC_EFFORTS = ("low", "medium", "high", "xhigh", "max")
OPENAI_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
TIERS = ("plan", "answer", "complex", "default", "escalation")


@pytest.fixture(autouse=True)
def _no_switch_env(monkeypatch):
    """Each test starts with the switch variable unset."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    yield


@pytest.fixture(autouse=True)
def _fresh_settings():
    """load_settings is lru_cached: clear it before and after each test."""
    from utils.config import load_settings

    load_settings.cache_clear()
    yield
    load_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_schema_health():
    """The schema-health aggregate is process-global: start and end each test empty."""
    from llm.schema_health import reset_schema_health

    reset_schema_health()
    yield
    reset_schema_health()


# ── fake provider SDKs (spec Notes, C3–C5) ───────────────────────────────────────────

def make_fake_openai() -> types.ModuleType:
    """A stand-in for the ``openai`` SDK module.

    ``OpenAI(**kwargs)`` accepts any keywords; ``chat.completions.create(**kwargs)`` records
    the kwargs in ``CALLS`` and returns a namespace built from the module's ``REPLY`` dict
    (text, finish_reason, prompt / completion / cached / reasoning tokens, model).
    """
    mod = types.ModuleType("openai")
    mod.CALLS = []
    mod.REPLY = {"text": "fake answer", "finish_reason": "stop", "prompt_tokens": 100,
                 "completion_tokens": 20, "cached_tokens": 0, "reasoning_tokens": 0,
                 "model": None}

    class _Completions:
        def create(self, **kwargs):
            mod.CALLS.append(kwargs)
            r = mod.REPLY
            usage = types.SimpleNamespace(
                prompt_tokens=r["prompt_tokens"], completion_tokens=r["completion_tokens"],
                total_tokens=r["prompt_tokens"] + r["completion_tokens"],
                prompt_tokens_details=types.SimpleNamespace(cached_tokens=r["cached_tokens"]),
                completion_tokens_details=types.SimpleNamespace(
                    reasoning_tokens=r["reasoning_tokens"]))
            message = types.SimpleNamespace(content=r["text"], role="assistant")
            choice = types.SimpleNamespace(index=0, message=message,
                                           finish_reason=r["finish_reason"])
            served = r["model"] if r["model"] is not None else kwargs.get("model")
            return types.SimpleNamespace(choices=[choice], usage=usage, model=served)

    class OpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = types.SimpleNamespace(completions=_Completions())

    mod.OpenAI = OpenAI
    return mod


def make_fake_anthropic() -> types.ModuleType:
    """A stand-in for the ``anthropic`` SDK module.

    ``Anthropic(**kwargs)`` accepts any keywords; ``messages.create(**kwargs)`` records the
    kwargs in ``CALLS`` and returns a namespace built from the module's ``REPLY`` dict (text,
    stop_reason, refusal category, input / output tokens, model).
    """
    mod = types.ModuleType("anthropic")
    mod.CALLS = []
    mod.REPLY = {"text": "fake answer", "stop_reason": "end_turn", "category": None,
                 "input_tokens": 100, "output_tokens": 20, "model": None}

    class _Messages:
        def create(self, **kwargs):
            mod.CALLS.append(kwargs)
            r = mod.REPLY
            usage = types.SimpleNamespace(input_tokens=r["input_tokens"],
                                          output_tokens=r["output_tokens"],
                                          cache_read_input_tokens=0,
                                          cache_creation_input_tokens=0)
            blocks = [types.SimpleNamespace(type="text", text=r["text"])] if r["text"] else []
            details = (types.SimpleNamespace(type="refusal", category=r["category"],
                                             explanation=None)
                       if r["stop_reason"] == "refusal" else None)
            served = r["model"] if r["model"] is not None else kwargs.get("model")
            return types.SimpleNamespace(content=blocks, stop_reason=r["stop_reason"],
                                         stop_details=details, usage=usage, model=served)

    class Anthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.messages = _Messages()

    mod.Anthropic = Anthropic
    return mod


_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
_MAKERS = {"openai": make_fake_openai, "anthropic": make_fake_anthropic}


@contextmanager
def install_fake(provider: str):
    """Install a fresh fake SDK for ``provider`` and a dummy API key; restore on exit.

    Usable inside a hypothesis example (no function-scoped fixture needed).
    """
    mod = _MAKERS[provider]()
    key = _KEY_ENV[provider]
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(sys.modules, provider, mod)
        mp.delenv(key + "_FILE", raising=False)
        mp.setenv(key, "test-key-not-real")
        yield mod


@pytest.fixture
def fake_openai():
    with install_fake("openai") as mod:
        yield mod


@pytest.fixture
def fake_anthropic():
    with install_fake("anthropic") as mod:
        yield mod


# ── settings helpers ─────────────────────────────────────────────────────────────

def llm_settings(provider: str = "openai", *, options: dict | None = None,
                 replacement_options: dict | None = None, use_replacement: bool = False,
                 **extra):
    """LLMSettings over the code-default model ids, with per-tier options.

    ``options`` / ``replacement_options`` map provider -> {tier: TierOptions kwargs} and go
    into ``profiles`` / ``replacement_profiles`` respectively.
    """
    from utils.config import LLMSettings, default_profiles

    def group(base: dict, opts: dict | None) -> dict:
        out = {}
        for prov, tiers in base.items():
            entry = dict(tiers)
            if opts and prov in opts:
                entry["tier_options"] = opts[prov]
            out[prov] = entry
        return out

    return LLMSettings.model_validate({
        "provider": provider, "use_replacement_models": use_replacement,
        "profiles": group(default_profiles(False), options),
        "replacement_profiles": group(default_profiles(True), replacement_options),
        **extra})


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@contextmanager
def capture_warnings():
    """Collect every WARNING-or-above record that reaches the root logger, plus those on
    ``llm.calls`` and ``llm.usage`` directly (handler attached, so capture does not depend
    on the logging configuration)."""
    handler = _ListHandler()
    names = ("", "llm.calls", "llm.usage")
    saved = []
    for name in names:
        lg = logging.getLogger(name)
        saved.append((lg, lg.level, lg.disabled))
        lg.addHandler(handler)
        lg.disabled = False
        if name:
            lg.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        for lg, level, disabled in saved:
            lg.removeHandler(handler)
            lg.setLevel(level)
            lg.disabled = disabled


def warnings_in(records) -> int:
    """Distinct WARNING-or-above records (a record may reach the handler twice)."""
    return len({id(r) for r in records if r.levelno >= logging.WARNING})


# ── in-memory pipeline (C7, C8), as in the cache-scope / answer-usage-cost conftests ──

FED, BIS = "Federal Reserve", "Bank for International Settlements"
QUERY = "how did treasury yields behave"     # no quantitative trigger word: no agent
FIXED_BUILT_AT = "2026-01-02T03:04:05+00:00"
DOCS = {"fed_note": (FED, "Treasury yields fell as markets priced cuts."),
        "bis_note": (BIS, "Treasury yields fell across advanced economies.")}


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


def _base_purpose(purpose: str | None) -> str:
    """``generate:escalated:repair`` -> ``generate``."""
    return (purpose or "-").split(":")[0]


class TierRecorder(LLMClient):
    """Provider-boundary double: answers each purpose with schema-valid JSON and logs
    ``(purpose, model, llm.usage.current_tier())`` for every call.

    ``route`` is what the plan call returns. Generation cites the Fed chunk; the verifier
    marks every claim unsupported (so an enabled escalation fires on prose); synthesis
    emits no fields (so an enabled escalation fires on synthesis when a higher tier exists);
    multi-hop is done after one hop.
    """

    def __init__(self, route: str = "DIRECT", model: str = "gpt-5.4-nano"):
        self.model = model
        self.route = route
        self.log: list[tuple[str, str | None, str | None]] = []

    def complete(self, *, system: str, user: str, temperature: float | None = None,
                 model: str | None = None, purpose: str | None = None) -> str:
        from llm.usage import current_tier

        self.log.append((purpose or "-", model, current_tier()))
        base = _base_purpose(purpose)
        if base == "plan":
            return json.dumps({"route": self.route, "paraphrase": "", "subqueries": [],
                               "hyde": []})
        if base == "generate":
            return json.dumps({"summary": "Yields fell.",
                               "details": [{"text": "Yields fell.",
                                            "chunk_ids": ["fed_note::000"]}],
                               "unanswered": []})
        if base == "verify":
            return json.dumps({"results": [{"index": 0, "supported": False}]})
        if base == "synthesize":
            return json.dumps({"shock_vector": {}})
        if base == "multi_hop":
            return json.dumps({"done": True})
        if base == "discover_factors":
            return json.dumps({"factors": []})
        return "{}"


def pipeline_settings(*, escalation: bool = False, verify: bool = True,
                      escalation_model: str | None = None, multi_hop: int = 0,
                      llm=None):
    """RagSettings for the in-memory pipeline (no hydration, no semantic cache)."""
    from utils.config import RagSettings

    s = RagSettings()
    if llm is not None:
        s.llm = llm
    if escalation_model is not None:
        s.llm.escalation_model = escalation_model
    s.retrieval.verify_faithfulness = verify
    s.retrieval.hydrate_timeseries = False
    s.retrieval.multi_hop_max_hops = multi_hop
    s.escalation.enabled = escalation
    s.escalation.cost_budget_usd = 1e6
    s.semantic_cache.enabled = False
    return s


def build(root, llm, settings, *, l1=None, digest: str = "abcdef012345"):
    """A pipeline over the two notes driven by ``llm`` with an LLM query analyzer (planning
    on ``settings.llm.plan_model``); L1 defaults to NullCache, no L2."""
    from embeddings.hashing import HashingEmbedder
    from llm.understanding import LLMAnalyzer
    from pipelines.query_pipeline import QueryPipeline
    from retriever.rerank import IdentityReranker
    from retriever.retriever import Retriever
    from utils.cache import NullCache
    from utils.config import RetrievalSettings
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
    rset = RetrievalSettings(final_k=10)
    analyzer = LLMAnalyzer(llm, rset, plan_model=settings.llm.plan_model)
    retriever = Retriever(embedder, store, IdentityReranker(), analyzer=analyzer,
                          settings=rset)
    settings.paths.manifest = str(root / "vectorstore" / "manifest.json")
    (root / "vectorstore").mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(settings, chunks=len(points), digest=digest)
    manifest["built_at"] = FIXED_BUILT_AT
    write_manifest(settings.manifest_path, manifest)
    return QueryPipeline(retriever, llm, None, settings,
                         tracer=Tracer(root / "trace.jsonl", enabled=False),
                         cache=l1 if l1 is not None else NullCache(), semantic_cache=None)


def expected_tier(purpose: str, model: str | None, route: str, llm) -> str | None:
    """The tier the Requirement ("Call sites set the tier") assigns to one call."""
    head = purpose.split(":repair")[0]
    if head in ("plan", "plan_execute", "multi_hop", "verify"):
        return "plan"
    if head == "generate":
        return "complex" if route in llm.complex_routes else "answer"
    if head in ("synthesize", "discover_factors"):
        return "complex"
    if head in ("generate:escalated", "synthesize:escalated"):
        return "complex" if model == llm.complex_model else "escalation"
    return None


@pytest.fixture(scope="session")
def lob():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        ANTHROPIC_EFFORTS=ANTHROPIC_EFFORTS, OPENAI_EFFORTS=OPENAI_EFFORTS, TIERS=TIERS,
        RAG_YAML=RAG_YAML, QUERY=QUERY, install_fake=install_fake, llm_settings=llm_settings,
        capture_warnings=capture_warnings, warnings_in=warnings_in, TierRecorder=TierRecorder,
        pipeline_settings=pipeline_settings, build=build, expected_tier=expected_tier)
