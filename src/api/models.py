"""API request/response models."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from llm.usage import UsageAggregate
from utils.config import normalize_api_tags
from utils.schema import Answer, MetaFilter, QueryAnalysis, RetrievedChunk

# §L DoS bounds: cap unbounded caller-supplied strings/lists so a single request
# can't exhaust memory/LLM cost. Generous, but finite (pydantic → HTTP 422 on breach).
_Q_MAX = 4_000        # question
_TEXT_MAX = 8_000     # scenario / portfolio narrative
_NOTE_MAX = 2_000     # feedback note
_TAG_MAX = 200        # one facet token
_LIST_MAX = 100       # a facet list / fields list
_DATE_MAX = 32        # ISO date string


class FilterModel(BaseModel):
    tiers: list[int] = Field(default_factory=list, max_length=_LIST_MAX)
    buckets: list[str] = Field(default_factory=list, max_length=_LIST_MAX)
    doc_types: list[str] = Field(default_factory=list, max_length=_LIST_MAX)
    entity_tags: list[str] = Field(default_factory=list, max_length=_LIST_MAX)
    factor_tags: list[str] = Field(default_factory=list, max_length=_LIST_MAX)
    publishers: list[str] = Field(default_factory=list, max_length=_LIST_MAX)
    date_from: str | None = Field(default=None, max_length=_DATE_MAX)
    date_to: str | None = Field(default=None, max_length=_DATE_MAX)

    def to_meta(self) -> MetaFilter:
        data = self.model_dump()
        # normalize caller facet tokens to MacroRAG's controlled vocabulary
        data["factor_tags"] = normalize_api_tags(data["factor_tags"])
        data["entity_tags"] = normalize_api_tags(data["entity_tags"])
        return MetaFilter(**data)


class SynthesisOptions(BaseModel):
    """Structured controls for ``mode="synthesize"`` (all optional; when omitted
    the synthesizer infers everything from the free-text question, as before).

    - ``fields``: restrict the emitted shock vector to these field names (a subset
      of the contract vocabulary; see ``GET /synthesis/schema``). Empty = all.
    - ``horizon_days``: the scenario horizon; moves are cumulative over it. Lets a
      caller pin "10-day shocks" structurally instead of in prose.
    - ``scenario``: extra scenario narrative folded into retrieval + synthesis,
      so a generic ``question`` can carry the detail here.
    - ``directions``: the scenario's expected sign per field, ``"up"`` (a positive
      value under the field's ``positive_means`` convention) or ``"down"``. A field
      whose sign contradicts it is flagged ``direction_mismatch`` (hard).
    """
    fields: list[str] = Field(default_factory=list, max_length=_LIST_MAX)
    horizon_days: int | None = Field(default=None, gt=0, le=36_500)
    scenario: str | None = Field(default=None, max_length=_TEXT_MAX)
    directions: dict[str, Literal["up", "down"]] = Field(default_factory=dict,
                                                          max_length=_LIST_MAX)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=_Q_MAX)
    filters: FilterModel | None = None
    mode: str = "answer"             # "answer" (default) | "synthesize" (spec §3.1)
    synthesis: SynthesisOptions | None = None   # structured controls for synthesize mode
    no_cache: bool = False           # skip the answer caches, read and write (spec answer-cache-bypass)


class FactorRequest(BaseModel):
    """Discover the macro risk factors relevant to a portfolio/theme."""
    portfolio: str = Field(min_length=1, max_length=_TEXT_MAX)  # free-text portfolio/theme
    filters: FilterModel | None = None
    top_k: int = Field(default=8, ge=1, le=50)


class CitationsRequest(BaseModel):
    """Batch provenance lookup — resolve many cited chunk_ids in one call (e.g.
    verify every chunk a shock vector / factor list cites)."""
    chunk_ids: list[str] = Field(default_factory=list, max_length=200)


class FeedbackRequest(BaseModel):
    question: str = Field(min_length=1, max_length=_Q_MAX)
    rating: int                      # +1 / -1 (thumbs). Positive, chunk-citing feedback
    #                                  seeds golden CANDIDATES via /admin/feedback/reconcile
    #                                  (review-gated; never auto-promoted) — see eval/feedback_reconcile.py
    note: str | None = Field(default=None, max_length=_NOTE_MAX)
    chunk_ids: list[str] = Field(default_factory=list, max_length=_LIST_MAX)


# ── serialization helpers ─────────────────────────────────────────────────────

_MAX_POINTS_PER_SERIES = 200   # §E/§L: bound the charting payload per (variable, scenario)


def timeseries_hit_to_dict(h) -> dict:
    """Serialize a hydrated numeric hit WITH its data points (§E), grouped into
    plottable ``series`` by (variable, scenario) so the UI SeriesChart can render —
    previously only ``n_rows`` was returned, so the chart had nothing to draw."""
    groups: dict[tuple, list] = {}
    for r in h.rows:
        key = (r.get("variable") or "?", r.get("scenario") or "")
        groups.setdefault(key, []).append(r)
    series = []
    for (var, scen), rows in groups.items():
        points = [{"period": r.get("period"), "value": r.get("value")}
                  for r in rows[:_MAX_POINTS_PER_SERIES] if r.get("value") is not None]
        series.append({"variable": var, "scenario": scen or None,
                       "points": points, "n_points": len(points)})
    return {"source": h.source, "publisher": h.publisher, "note": h.note,
            "n_rows": len(h.rows), "series": series}


def answer_to_dict(answer: Answer) -> dict:
    return {
        "summary": answer.summary,
        "refused": answer.refused,
        "details": [{"text": d.text, "chunk_ids": d.chunk_ids} for d in answer.details],
        "unanswered": answer.unanswered,
        "unsupported": answer.unsupported,
        "used_chunk_ids": answer.used_chunk_ids,
        "timeseries": [timeseries_hit_to_dict(h) for h in answer.timeseries],
    }


def chunk_brief(chunk: RetrievedChunk) -> dict:
    p = chunk.payload
    return {"chunk_id": chunk.chunk_id, "source_id": p.get("source_id"),
            "publisher": p.get("publisher"), "publication": p.get("publication"),
            "release_date": p.get("release_date"), "doc_type": p.get("doc_type"),
            "rerank_score": chunk.rerank_score, "expanded": bool(p.get("expanded"))}


def citation_to_dict(chunk: RetrievedChunk) -> dict:
    """Full cited-chunk view (provenance brief + text + url/section). Shared by the
    single ``GET /citations/{id}`` and batch ``POST /citations`` endpoints."""
    return {**chunk_brief(chunk), "text": chunk.text,
            "url": chunk.payload.get("url"), "section": chunk.payload.get("section")}


def resolve_citations(store, chunk_ids: list[str]) -> dict:
    """Batch provenance resolution shared by REST + MCP: preserve request order,
    dedupe, and report unresolved ids in ``missing`` (no error on a partial set)."""
    wanted = list(dict.fromkeys(chunk_ids))
    if not wanted:
        return {"citations": [], "missing": []}
    by_id = {c.chunk_id: c for c in store.get_by_chunk_ids(wanted)}
    return {
        "citations": [citation_to_dict(by_id[cid]) for cid in wanted if cid in by_id],
        "missing": [cid for cid in wanted if cid not in by_id],
    }


def validate_shock_fields(fields: list[str] | None) -> list[str] | None:
    """Uppercase + validate caller-requested shock fields against the contract
    vocabulary (shared by REST + MCP). Raises ``ValueError`` listing the unknown
    names; empty/None -> no restriction. Callers map the error to their transport
    (HTTP 400 / MCP tool error)."""
    if not fields:
        return None
    from synthesis import SHOCK_FIELDS
    out: list[str] = []
    unknown: list[str] = []
    for f in fields:
        name = str(f).upper()
        (out if name in SHOCK_FIELDS else unknown).append(name)
    if unknown:
        raise ValueError(f"unknown shock field(s) {unknown}; "
                         f"valid: {sorted(SHOCK_FIELDS)}")
    return out


def validate_shock_directions(directions: dict[str, str] | None) -> dict[str, str] | None:
    """Uppercase + validate caller-requested field directions (shared by REST + MCP).

    Args:
        directions: Shock field name -> "up" or "down"; empty/None -> no check.

    Returns:
        The directions keyed by upper-case field name, or None when empty.

    Raises:
        ValueError: Listing unknown field names or values other than "up"/"down".
            Callers map it to their transport (HTTP 400 / MCP tool error).
    """
    if not directions:
        return None
    names = validate_shock_fields(list(directions)) or []
    bad = sorted({str(v) for v in directions.values() if v not in ("up", "down")})
    if bad:
        raise ValueError(f"direction value(s) {bad} must be 'up' or 'down'")
    return dict(zip(names, directions.values(), strict=True))


def cache_opts(no_cache: bool) -> dict[str, bool]:
    """Pipeline keywords for a request's cache bypass (spec answer-cache-bypass).

    The keyword is passed only when set, so a pipeline object that predates it
    (an embedding caller's own or a test double) still serves default requests.

    Args:
        no_cache: The request's ``no_cache`` flag.

    Returns:
        ``{"no_cache": True}`` when set, else an empty dict.
    """
    return {"no_cache": True} if no_cache else {}


def usage_to_dict(agg: UsageAggregate | None, *, cached: bool) -> dict[str, Any]:
    """The response's ``usage`` block for a request's usage aggregate (spec answer-usage-cost).

    Never carries prompts, responses or the per-call log. ``cost_usd`` sums the
    priced calls only; ``cost_complete`` is false when some call was unpriced.

    Args:
        agg: The request's aggregate, or None when none was open.
        cached: The response's answer-cache flag.

    Returns:
        The usage block: cost, cost completeness, call and token counts, and ``cached``.
    """
    if agg is None:
        agg = UsageAggregate(label="none")
    return {"cost_usd": float(agg.cost_usd), "cost_complete": agg.cost_complete,
            "calls": agg.calls, "prompt_tokens": agg.prompt_tokens,
            "completion_tokens": agg.completion_tokens, "cached_tokens": agg.cached_tokens,
            "total_tokens": agg.prompt_tokens + agg.completion_tokens, "cached": cached}


def result_to_dict(answer: Answer, chunks: list[RetrievedChunk],
                   analysis: QueryAnalysis, *, cached: bool) -> dict:
    return {
        **answer_to_dict(answer),
        "route": analysis.route,
        "cached": cached,
        "chunks": [chunk_brief(c) for c in chunks],
    }


def factors_to_dict(result) -> dict:
    """Factor-discovery response: ranked, cited factors + the evidence chunks they
    were drawn from + the corpus snapshot id. Every factor's chunk_ids appear in
    chunks[] (post-gate), so the consumer can hydrate provenance."""
    from factors import factors_to_dict as _factors
    body = {
        "factors": _factors(result.factors),
        "route": result.analysis.route,
        "chunks": [chunk_brief(c) for c in result.chunks],
    }
    if result.world_view_id:
        body["world_view_id"] = result.world_view_id
    return body


def synthesis_to_dict(query_result, shock_vector: dict,
                      world_view_id: str | None, diagnostics=None) -> dict:
    """Synthesis response: all normal /query fields + the structured shock vector
    (spec §3.2). Every shock field cites chunk_ids present in chunks[].
    ``diagnostics`` surfaces WHY fields did/didn't ground (grounding outcome +
    per-field drop reasons) so a consumer isn't left guessing on an empty vector."""
    body = result_to_dict(query_result.answer, query_result.chunks,
                          query_result.analysis, cached=query_result.cached)
    body["shock_vector"] = {name: f.to_dict() for name, f in shock_vector.items()}
    if world_view_id:
        body["world_view_id"] = world_view_id
    if diagnostics is not None:
        body["synthesis_diagnostics"] = diagnostics.as_dict()
    return body
