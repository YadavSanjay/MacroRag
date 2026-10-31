"""In-process MCP server exposing MacroRAG's query / synthesis / discovery
surface as self-describing tools for an LLM-driven consumer (e.g. Faultline's
ScenarioGen).

The tools call the query pipeline **in-process** (same Python, no HTTP hop) and
return the *identical* JSON the REST endpoints return — they reuse the
``api.models`` serializers and shared helpers, so the REST and MCP contracts can
never drift. The pipeline is built lazily on first tool call (heavy models load on
demand), mirroring ``api.app.create_app``; tests inject a fake factory.

Run it over ``stdio`` (default, for a local subprocess integration) or ``sse`` /
``streamable-http`` to expose it on a port — see ``mcp_server.__main__``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Optional

import anyio
from mcp.server.fastmcp import FastMCP

from api.discovery import vocabulary_view
from api.models import (
    FilterModel,
    cache_opts,
    factors_to_dict,
    resolve_citations,
    result_to_dict,
    synthesis_to_dict,
    usage_to_dict,
    validate_shock_directions,
    validate_shock_fields,
)
from llm.usage import track_usage
from utils.config import RagSettings, load_settings
from utils.logging import get_logger
from utils.schema import MetaFilter

log = get_logger(__name__)


def _meta(filters: FilterModel | None,
          principal: str | None) -> MetaFilter | None:
    """Build the retrieval MetaFilter from caller facets + the server's principal."""
    base = filters.to_meta() if filters else None
    if principal:
        base = base or MetaFilter()
        base.tenant_id = principal
    return base


def build_mcp(pipeline_factory: Callable[[], Any] | None = None,
              settings: RagSettings | None = None,
              *, principal: str | None = None, warmup: bool = False) -> FastMCP:
    """Wire the MacroRAG MCP server. ``pipeline_factory`` defaults to the full
    production pipeline (lazy); ``principal``, when set, stamps that caller identity
    on every retrieval filter's dormant ``tenant_id`` hook (the MCP analogue of an
    API key's principal mapping). Note the corpus is a shared public knowledge
    base — no ingest path writes ``tenant_id``, so ``principal`` is an identity,
    NOT a data-isolation boundary today
    (see docs/design/Macro_RAG_Production_Hardening.md §A).

    The tools are ``async`` and run the (blocking, CPU-heavy) pipeline in a worker
    thread via ``anyio.to_thread`` so the stdio transport's event loop is never
    frozen — otherwise a single cold query (model load + ~60s CPU rerank, blocking
    Qdrant/OpenAI I/O) would stall the JSON-RPC reader and the client would time
    out the call. ``warmup=True`` builds the pipeline AND warms it (loads the
    bge-m3 embedder + cross-encoder reranker and runs one inference through each)
    on a background thread at startup, so the first tool call doesn't pay the
    one-time model-load + cold-inference cost — the dominant term in the multi-
    minute first call clients have observed. It never blocks server start-up or
    the ``initialize`` handshake."""
    settings = settings or load_settings()
    state: dict = {}
    lock = threading.Lock()

    def pipe():
        # Double-checked locking: a warmup thread and the first tool call can race
        # to build; we want exactly one (expensive) build, shared by both.
        p = state.get("p")
        if p is not None:
            return p
        with lock:
            if "p" not in state:
                if pipeline_factory is not None:
                    state["p"] = pipeline_factory()
                else:
                    log.info("building query pipeline...")
                    from pipelines.query_pipeline import build_pipeline
                    state["p"] = build_pipeline(settings)
            return state["p"]

    if warmup:
        def _warm():
            try:
                p = pipe()
                warm = getattr(p, "warm", None)
                if callable(warm):
                    warm()        # load + run one inference on the heavy models
                log.info("query pipeline warmed (models loaded; first call is hot)")
            except Exception:  # pragma: no cover - log, don't crash the server
                log.exception("pipeline warmup failed; will retry on first tool call")
        threading.Thread(target=_warm, name="mcp-warmup", daemon=True).start()

    mcp = FastMCP("macro-rag")

    @mcp.tool()
    async def macro_query(question: str,
                          filters: FilterModel | None = None,
                          no_cache: bool = False) -> dict:
        """Answer a macro question from the cited corpus. Returns a grounded prose
        summary, structured details with chunk_ids, hydrated time-series, the
        chosen route, and the retrieved chunks (each with provenance). Use
        macro_vocabulary() to discover valid filter tags. ``no_cache`` skips the
        answer caches (neither read nor written)."""
        def _run():
            with track_usage("mcp-query") as agg:
                r = pipe().answer(question, base_filter=_meta(filters, principal),
                                  **cache_opts(no_cache))
            out = result_to_dict(r.answer, r.chunks, r.analysis, cached=r.cached)
            out["usage"] = usage_to_dict(agg, cached=r.cached)   # spec answer-usage-cost
            return out
        return await anyio.to_thread.run_sync(_run)

    @mcp.tool()
    async def macro_synthesize_shock_vector(
        question: str,
        fields: list[str] | None = None,
        horizon_days: int | None = None,
        scenario: str | None = None,
        filters: FilterModel | None = None,
        directions: dict[str, str] | None = None,
        no_cache: bool = False,
    ) -> dict:
        """Turn a macro scenario into a machine-readable, citation-gated SHOCK
        VECTOR for a stress engine. Each emitted field is signed, carries a fixed
        unit, and cites ≥1 dated chunk (ungrounded fields are dropped, not
        guessed). Optionally restrict the output to ``fields`` (see
        macro_shock_schema for valid names/units), pin ``horizon_days``, or pass
        extra ``scenario`` narrative. ``directions`` (field -> "up"/"down") drops
        or flags any field whose sign contradicts the scenario's stated direction
        (``direction_mismatch``). Also returns ``world_view_id`` — the corpus
        snapshot — pin downstream results to it for reproducibility. ``no_cache``
        skips the answer caches for the retrieval step (neither read nor written)."""
        valid = validate_shock_fields(fields)   # raises ValueError on unknown field
        dirs = validate_shock_directions(directions)   # ValueError on unknown name/value
        def _run():
            with track_usage("mcp-synthesize") as agg:
                syn = pipe().synthesize(question, base_filter=_meta(filters, principal),
                                        fields=valid, horizon_days=horizon_days,
                                        scenario=scenario, directions=dirs,
                                        **cache_opts(no_cache))
            out = synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
                                    diagnostics=syn.diagnostics)
            out["usage"] = usage_to_dict(agg, cached=syn.query.cached)
            return out
        return await anyio.to_thread.run_sync(_run)

    @mcp.tool()
    async def macro_discover_factors(portfolio: str, top_k: int = 8,
                                     filters: FilterModel | None = None) -> dict:
        """Explore which macro risk factors are relevant to a portfolio/theme —
        the exploration step that precedes shock synthesis. Returns a ranked,
        citation-gated list of controlled factor tags (each with a relevance
        grade, related entity tags, rationale, and chunk_ids), the evidence
        chunks, and the corpus ``world_view_id``."""
        def _run():
            res = pipe().discover_factors(portfolio, base_filter=_meta(filters, principal),
                                          top_k=top_k)
            return factors_to_dict(res)
        return await anyio.to_thread.run_sync(_run)

    @mcp.tool()
    async def macro_get_citations(chunk_ids: list[str]) -> dict:
        """Resolve cited chunk_ids to their full text + provenance in one call
        (e.g. verify every chunk a shock vector or factor list cites). Returns
        ``citations`` (order-preserving, deduped) and ``missing`` (ids that no
        longer resolve, e.g. after a re-index)."""
        def _run():
            return resolve_citations(pipe().retriever.store, chunk_ids)
        return await anyio.to_thread.run_sync(_run)

    @mcp.tool()
    async def macro_vocabulary() -> dict:
        """The controlled facet vocabulary — ``factor_tags``, ``entity_tags``,
        ``doc_types``, and the ``aliases`` map — to construct valid ``filters``
        for the other tools."""
        return vocabulary_view()

    @mcp.tool()
    async def macro_shock_schema() -> dict:
        """The shock-field catalog consumed by macro_synthesize_shock_vector:
        every valid field name with its allowed/preferred unit and positive-sign
        convention. Treat this as the source of truth, not a hard-coded table."""
        from synthesis import schema_dict
        return schema_dict()

    return mcp
