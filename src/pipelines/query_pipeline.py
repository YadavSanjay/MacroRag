"""Query pipeline: question -> retrieve (hybrid + filters + rerank) -> hydrate
numeric series -> cited LLM answer. Components injected for prod/test parity."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:                       # import-time only; avoids a cycle
    from retriever.rerank import Reranker  # noqa: F401
    from synthesis import SynthesisDiagnostics  # noqa: F401

from llm.base import LLMClient
from llm.escalation import EscalationPolicy, escalation_event, prose_score
from llm.generate import generate_answer
from llm.prompts import load_prompt
from llm.usage import configure_call_capture, tier_context, track_usage, usage_snapshot
from llm.verify import verify_answer
from retriever.retriever import Retriever
from utils.cache import Cache, SemanticCache, build_cache, build_semantic_cache, cache_key
from utils.config import RagSettings, load_settings
from utils.logging import get_logger
from utils.observability import span
from utils.schema import Answer, MetaFilter, QueryAnalysis, RetrievedChunk
from utils.trace import Timer, Tracer, build_synthesis_trace, build_trace
from vectorstores.filters import match_payload
from vectorstores.timeseries_store import TimeseriesStore

from .hydrate import hydrate_timeseries

log = get_logger(__name__)


@dataclass
class QueryResult:
    answer: Answer
    chunks: list[RetrievedChunk]
    analysis: QueryAnalysis
    cached: bool = False


@dataclass
class SynthesisResult:
    """A normal query result plus the structured, gated shock vector (the
    AITrade scenario-synthesis contract) and the grounding diagnostics."""
    query: QueryResult
    shock_vector: dict          # field -> ShockField
    world_view_id: str | None
    diagnostics: SynthesisDiagnostics | None = None


@dataclass
class FactorDiscoveryResult:
    """Ranked, gated macro risk factors relevant to a portfolio/theme, with the
    evidence chunks they were drawn from (the upstream exploration step)."""
    factors: list               # list[RelevantFactor]
    chunks: list[RetrievedChunk]
    analysis: QueryAnalysis
    world_view_id: str | None


def _scenario_study_filter(base: MetaFilter | None) -> MetaFilter:
    """The synthesize retrieval filter: ``base`` plus the scenario-study exemption,
    so heuristic entity tags cannot exclude the quantified analogs. Caller-supplied
    entity tags stay hard (``extract_filters`` drops the exemption for them)."""
    from synthesis.vocab import SCENARIO_STUDY_TAGS

    mf = dc_replace(base) if base is not None else MetaFilter()
    if not mf.entity_tags:
        mf.entity_exempt_factor_tags = list(SCENARIO_STUDY_TAGS)
    return mf


def _scrub_world_view(qr: QueryResult, wv: str | None) -> QueryResult:
    """Rewrite world_view_id claims in the prose to the real id. Copies rather than
    mutates: the answer object may be shared with the result cache."""
    from llm.sanitize import scrub_world_view_claims

    a = qr.answer
    answer = dc_replace(
        a, summary=scrub_world_view_claims(a.summary, wv),
        details=[dc_replace(d, text=scrub_world_view_claims(d.text, wv))
                 for d in a.details])
    return dc_replace(qr, answer=answer)


def cache_version(settings: RagSettings) -> str:
    """The answer-cache version for these settings (spec llm-output-budget C8).

    Args:
        settings: The RAG settings.

    Returns:
        A hex digest over the answer prompt, the plan / answer / complex models,
        ``llm.max_tokens`` and the active profile's options for those three tiers.
    """
    import hashlib

    from utils.config import tier_options_for

    llm = settings.llm
    parts = [load_prompt("answer"), llm.plan_model, llm.answer_model, llm.complex_model,
             str(llm.max_tokens)]
    parts += [tier_options_for(llm, t).model_dump_json() for t in ("plan", "answer", "complex")]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


class QueryPipeline:
    def __init__(self, retriever: Retriever, llm: LLMClient,
                 timeseries: TimeseriesStore | None = None,
                 settings: RagSettings | None = None,
                 tracer: Tracer | None = None,
                 cache: Cache | None = None,
                 semantic_cache: SemanticCache | None = None,
                 agent=None):
        self.retriever = retriever
        self.llm = llm
        self.timeseries = timeseries
        self.agent = agent          # PlanExecuteAgent for quantitative queries
        self.s = settings or load_settings()
        self.tracer = tracer or Tracer(self.s.trace_path, enabled=self.s.trace.enabled)
        # Prompt/response capture into the per-query call log (Trace Explorer).
        configure_call_capture(enabled=self.s.trace.capture_llm,
                               max_chars=self.s.trace.max_prompt_chars)
        self.escalation = EscalationPolicy(self.s.escalation, self.s.llm)
        self.cache = cache if cache is not None else build_cache(
            self.s.cache, shared_url=self.s.redis.url)
        self.semantic = semantic_cache
        if self.semantic is None:
            self.semantic = build_semantic_cache(self.s)   # None when disabled (§I)
        # Cache version: what shapes an answer (prompt, planner and generation models,
        # output cap, tier options), so any change invalidates.
        self._cache_version = cache_version(self.s)

    def answer(self, query: str, *, base_filter: MetaFilter | None = None,
               retrieve_opts: dict | None = None,
               use_agent: bool = True, no_cache: bool = False) -> QueryResult:
        """Answer ``query``. ``retrieve_opts`` are extra ``Retriever.retrieve``
        keywords (synthesize passes its rerank cap and reserved slots).
        ``use_agent=False`` skips the plan-execute route (synthesize does, so its
        filters and reserved slots always apply; spec synth-route-filters).
        ``no_cache=True`` neither reads nor writes the answer caches (spec
        answer-cache-bypass)."""
        with span("rag.query", query=query), track_usage("query"):
            return self._answer(query, base_filter, retrieve_opts or {}, use_agent,
                                no_cache=no_cache)

    def _answer(self, query: str, base_filter: MetaFilter | None,
                retrieve_opts: dict, use_agent: bool = True, *,
                no_cache: bool = False) -> QueryResult:
        # L1 exact cache. Retrieval options, the route and the corpus change the
        # evidence, so they key too; the corpus id is read per call so a re-index
        # made while the server runs invalidates (spec cache-scope).
        from utils.manifest import world_view_id
        version = self._cache_version + (
            repr(sorted(retrieve_opts.items())) if retrieve_opts else "") + (
            "" if use_agent else "|no-agent") + f"|{world_view_id(self.s) or ''}"
        key = cache_key(query, base_filter, version=version)
        hit = None if no_cache else self.cache.get(key)
        if hit is not None:
            return QueryResult(hit.answer, hit.chunks, hit.analysis, cached=True)

        # L2 semantic cache (near-identical query embedding), scoped to the same
        # filter, version and corpus as L1 (spec cache-scope)
        scope = cache_key("", base_filter, version=version)
        qvec = None
        if self.semantic is not None and not no_cache:
            qvec = self.retriever.embedder.embed_query(query).dense
            sem_hit = self.semantic.get(qvec, scope=scope)
            if sem_hit is not None:
                return QueryResult(sem_hit.answer, sem_hit.chunks, sem_hit.analysis,
                                   cached=True)

        timings: dict[str, float] = {}
        escalations: list[dict] = []
        chunks, analysis, hits = self._gather(query, base_filter, timings, retrieve_opts,
                                              use_agent=use_agent)

        # §D: scan the retrieved (external) text for prompt-injection markers. The
        # context itself is isolated in format_context; this records the signal
        # (log + metric + trace) so an attempt is visible, not silent.
        from llm.sanitize import note_injection
        security = note_injection("context", "\n".join(c.text or "" for c in chunks))

        # Refuse only when there is neither relevant text nor structured evidence.
        if self._below_floor(chunks) and not hits:
            answer = self._refusal(query)
        else:
            gen_model = self._generation_model(analysis.route)
            gen_tier = ("complex" if analysis.route in self.s.llm.complex_routes
                        else "answer")
            answer = self._generate_and_verify(query, chunks, hits, gen_model, timings,
                                               tier=gen_tier)
            # Quality-signal escalation: if the cheap pass deteriorated, retry on a
            # higher tier and keep only if better (§5). No-op unless enabled.
            answer, escalations = self._maybe_escalate_prose(
                query, chunks, hits, answer, gen_model, timings)

        self.tracer.write(build_trace(query, analysis, chunks, answer,
                                      timings=timings, usage=usage_snapshot(),
                                      escalations=escalations, security=security))
        result = QueryResult(answer=answer, chunks=chunks, analysis=analysis)
        # A refusal is never cached, so a transient one can recover (spec
        # answer-cache-bypass); qvec is None under no_cache, which skips L2.
        if not no_cache and not answer.refused:
            self.cache.set(key, result)
            if self.semantic is not None and qvec is not None:
                self.semantic.add(key, qvec, result, scope=scope)
        return result

    def synthesize(self, query: str, *, base_filter: MetaFilter | None = None,
                   fields: list[str] | None = None,
                   horizon_days: int | None = None,
                   scenario: str | None = None,
                   directions: dict[str, str] | None = None,
                   no_cache: bool = False) -> SynthesisResult:
        """Run the normal pipeline AND extract a structured, cited shock vector
        (spec §3). The prose answer is preserved; the shock vector is gated so
        every field cites ≥1 chunk present in chunks[] with a release_date.

        Optional structured controls: ``fields`` restricts the emitted vector,
        ``horizon_days`` pins the scenario horizon, and ``scenario`` is folded
        into the query so retrieval + synthesis both see the extra narrative.
        ``directions`` (field -> "up"/"down") flags any field whose sign contradicts
        it (``direction_mismatch``, hard; spec synth-direction). ``no_cache`` is
        passed to the query step (spec answer-cache-bypass)."""
        from synthesis import SynthesisDiagnostics
        from utils.manifest import world_view_id

        with track_usage("synthesize"):
            if scenario:                       # §D: user scenario is untrusted too
                from llm.sanitize import note_injection
                note_injection("scenario", scenario)
            effective = f"{query}\n\nScenario: {scenario}" if scenario else query
            qr = self.answer(effective, base_filter=_scenario_study_filter(base_filter),
                             retrieve_opts=self._synthesis_retrieve_opts(),
                             use_agent=False, no_cache=no_cache)
            escalations: list[dict] = []
            if qr.answer.refused:
                diag = SynthesisDiagnostics(skipped="upstream_refused")  # floor/refusal
                kept: dict = {}
            elif not qr.chunks:
                diag = SynthesisDiagnostics(skipped="no_chunks")         # nothing retrieved
                kept = {}
            else:
                model = self.s.llm.complex_model
                kept, diag = self._ground_shock(effective, qr, model, fields, horizon_days,
                                                directions=directions)
                # Quality-signal escalation: if too many emitted fields were gated
                # away (or none survived), re-ground on a higher tier, keep if it
                # grounds more fields (§5). No-op unless enabled AND a higher tier
                # exists (with default profiles, complex is already the top tier).
                kept, diag, escalations = self._maybe_escalate_synth(
                    effective, qr, model, fields, horizon_days, kept, diag,
                    directions=directions)
            wv = world_view_id(self.s)
            qr = _scrub_world_view(qr, wv)
            log.info("%s", diag.summary())
            self.tracer.write(build_synthesis_trace(
                effective, diag, world_view_id=wv, usage=usage_snapshot(),
                escalations=escalations))
            return SynthesisResult(query=qr, shock_vector=kept,
                                   world_view_id=wv, diagnostics=diag)

    def _synthesis_retrieve_opts(self) -> dict:
        """Synthesize's retrieval overrides: its own rerank cap (CPU rerank is
        ~8s/doc) and reserved winner slots for quantified scenario studies."""
        from synthesis.vocab import SCENARIO_STUDY_TAGS

        syn = self.s.synthesis
        return {"rerank_candidates": syn.rerank_candidates,
                "reserve_tags": SCENARIO_STUDY_TAGS,
                "reserve_slots": syn.scenario_study_slots}

    def _ground_shock(self, effective, qr, model, fields, horizon_days,
                      *, purpose: str = "synthesize",
                      directions: dict[str, str] | None = None):
        """Run the shock-vector LLM synthesis + citation gate + numeric validation +
        grounding reconciliation for one model, returning (kept, diag). Called once
        for the primary pass and again (higher tier) on escalation."""
        from synthesis import (
            SynthesisDiagnostics,
            check_analog_quality,
            check_card_values,
            check_direction,
            check_episode_mix,
            check_source_horizon,
            gate_shock_vector,
            reconcile_grounding_modes,
            synthesize_shock_vector,
            validate_shock_vector,
        )
        from synthesis.diagnostics import missing_fields
        diag = SynthesisDiagnostics()
        diag.n_chunks = len(qr.chunks)
        diag.n_dated_chunks = sum(1 for c in qr.chunks if c.payload.get("release_date"))
        tier = "complex" if purpose == "synthesize" else self._escalation_tier(model)
        with span("rag.synthesize", model=model), tier_context(tier):
            raw = synthesize_shock_vector(
                self.llm, effective, qr.chunks, qr.answer.timeseries, model=model,
                fields=fields, horizon_days=horizon_days, diag=diag, purpose=purpose,
                directions=directions)
        kept = gate_shock_vector(
            raw, qr.chunks, min_chunks=self.s.synthesis.min_citation_chunks, diag=diag)
        if not self.s.synthesis.enable_analog:   # strict direct-only mode
            for name in [n for n, f in kept.items()
                         if getattr(f, "grounding_mode", "direct") == "analog"]:
                diag.drop(name, "config", "analog_disabled")
                del kept[name]
        if self.s.synthesis.numeric_validation and kept:   # numeric/coherence pass
            # unit/scale slips, FX proxies, evidence horizons and signs against
            # the requested directions first, so drop_invalid below sees them
            check_analog_quality(kept, qr.chunks, diag=diag)
            if self.s.synthesis.horizon_check:
                check_source_horizon(kept, qr.chunks, horizon_days=horizon_days,
                                     tolerance=self.s.synthesis.horizon_tolerance,
                                     diag=diag)
            if self.s.synthesis.direction_check:
                check_direction(kept, directions, diag=diag)
            # a card-grounded field must carry the card's largest move
            check_card_values(kept, qr.chunks, diag=diag)
            validate_shock_vector(
                kept, horizon_days=horizon_days, timeseries=qr.answer.timeseries,
                reference_horizon_days=self.s.synthesis.validation_reference_horizon_days,
                drop_invalid=self.s.synthesis.drop_invalid, diag=diag)
            # surface-only: a vector assembled from several episode cards
            check_episode_mix(kept, qr.chunks, diag=diag)
        if self.s.synthesis.reconcile_grounding and kept:  # analog->direct fix-up
            reconcile_grounding_modes(kept, qr.answer.timeseries, diag=diag)
        diag.kept_fields = list(kept)
        diag.analog_fields = [n for n, f in kept.items()
                              if getattr(f, "grounding_mode", "direct") == "analog"]
        diag.missing_fields = missing_fields(fields, kept, diag.dropped)
        return kept, diag

    def _maybe_escalate_synth(self, effective, qr, model, fields, horizon_days,
                              kept, diag, *, directions: dict[str, str] | None = None):
        """Re-ground the shock vector on the next tier up if the gate loss signal
        fires; keep it only if it grounds strictly more fields. Returns
        (kept, diag, escalations)."""
        if not self.escalation.enabled:
            return kept, diag, []
        reason = self.escalation.synth_signal(diag)
        if reason is None:
            return kept, diag, []
        hi = self.escalation.next_model(model)
        if hi is None or not self.escalation.can_afford(0):
            return kept, diag, []
        with span("rag.escalate", stage="synthesize", to_model=hi, reason=reason):
            hi_kept, hi_diag = self._ground_shock(
                effective, qr, hi, fields, horizon_days, purpose="synthesize:escalated",
                directions=directions)
        improved = len(hi_kept) > len(kept)
        log.info("synth escalation [%s]: %s -> %s (kept %d -> %d, improved=%s)",
                 reason, model, hi, len(kept), len(hi_kept), improved)
        ev = escalation_event("synthesize", reason, model, hi, improved,
                              kept_before=len(kept), kept_after=len(hi_kept))
        if improved:
            return hi_kept, hi_diag, [ev]
        return kept, diag, [ev]

    def discover_factors(self, portfolio: str, *,
                         base_filter: MetaFilter | None = None,
                         top_k: int = 8) -> FactorDiscoveryResult:
        """Explore which macro risk factors are relevant to ``portfolio``: retrieve
        evidence, have the LLM rank controlled factor tags against it, then gate so
        every emitted factor cites ≥1 dated chunk present in chunks[]. This is the
        exploration step that precedes shock-vector synthesis — no prose answer is
        generated, so it is cheaper than a full /query."""
        from factors import discover_factors as run_discovery
        from factors import gate_factors
        from utils.manifest import world_view_id

        with span("rag.discover_factors", portfolio=portfolio), \
                track_usage("discover_factors"):
            with Timer(), span("rag.retrieve"):
                chunks, analysis = self.retriever.retrieve(
                    portfolio, base_filter=base_filter)
            with tier_context("complex"):
                raw = (run_discovery(self.llm, portfolio, chunks,
                                     model=self.s.llm.complex_model, top_k=top_k)
                       if chunks else [])
            kept = gate_factors(raw, chunks)
        return FactorDiscoveryResult(factors=kept, chunks=chunks, analysis=analysis,
                                     world_view_id=world_view_id(self.s))

    # ── startup warm-up ──────────────────────────────────────────────────────

    def warm(self) -> None:
        """Eagerly load the heavy local models (bge-m3 embedder + cross-encoder
        reranker) AND run one inference through each, so the first real query
        doesn't pay the cold-start cost. Both models load lazily on first use
        otherwise (see embeddings/bge_m3.py, retriever/rerank.py), and the first
        torch forward pass on a CPU box additionally pays kernel/thread init — on
        the order of minutes for the first tool call (see configs/rag.yaml). Just
        constructing the pipeline does NOT trigger this; an actual inference does.

        Best-effort and self-contained: it embeds/reranks a tiny probe (no Qdrant,
        no LLM, no corpus needed) and never raises — a warm failure must not stop
        the server from serving (the model simply loads on the first real call)."""
        try:
            self.retriever.embedder.embed_queries(["warmup"])
        except Exception:  # pragma: no cover - defensive; warm must never crash
            log.warning("embedder warmup failed; will load on first query",
                        exc_info=True)
        try:
            probe = RetrievedChunk(chunk_id="warmup", text="warmup probe",
                                   score=0.0, payload={})
            self.retriever.reranker.rerank("warmup", [probe])
        except Exception:  # pragma: no cover - defensive
            log.warning("reranker warmup failed; will load on first query",
                        exc_info=True)

    # ── evidence gathering: typed plan-execute or normal RAG ─────────────────

    def _gather(self, query, base_filter, timings, retrieve_opts=None, *,
                use_agent: bool = True):
        """Return (chunks, analysis, data_hits). Quantitative queries go through
        the plan-execute agent (structured lookups + calculator); everything else
        takes the normal hybrid-retrieval + cue-based hydration path. The agent's
        executor retrieves unfiltered, so its chunks are cut to ``base_filter``
        here (spec synth-route-filters)."""
        if self.agent is not None and use_agent:
            plan = self.agent.maybe_plan(query)
            if plan is not None:
                with Timer() as t, span("rag.plan_execute"):
                    res = self.agent.execute(plan, query)
                timings["plan_execute"] = t.elapsed
                analysis = QueryAnalysis(original=query, route="PLAN_EXECUTE",
                                         filters=base_filter or MetaFilter())
                hits = list(res.data_hits)
                computed = res.computed_hit()
                if computed is not None:
                    hits.append(computed)
                chunks = res.chunks
                if base_filter is not None:
                    chunks = [c for c in chunks if match_payload(base_filter, c.payload)]
                return chunks, analysis, hits

        if self.s.retrieval.multi_hop_max_hops > 1:
            from retriever.multihop import multi_hop_retrieve
            with Timer() as t, span("rag.multi_hop"):
                mh = multi_hop_retrieve(
                    self.retriever, self.llm, query,
                    max_hops=self.s.retrieval.multi_hop_max_hops,
                    base_filter=base_filter, model=self.s.llm.plan_model)
            timings["multi_hop"] = t.elapsed
            log.info("multi-hop route walked %d hop(s): %s", len(mh.hops), mh.hops)
            chunks, analysis = mh.chunks, mh.analysis
        else:
            with Timer() as t, span("rag.retrieve"):
                chunks, analysis = self.retriever.retrieve(
                    query, base_filter=base_filter, **(retrieve_opts or {}))
            timings["retrieve"] = t.elapsed
        hits = []
        if self.s.retrieval.hydrate_timeseries and self.timeseries is not None:
            hits = hydrate_timeseries(self.timeseries, query)
        return chunks, analysis, hits

    # ── generation + faithfulness verify ─────────────────────────────────────

    def _generate_and_verify(self, query, chunks, hits, model, timings,
                             *, purpose: str = "generate", tier: str = "answer") -> Answer:
        """Generate on ``model`` under model tier ``tier``, then verify on the plan tier."""
        gkey = "generate" if purpose == "generate" else "generate_escalated"
        vkey = "verify" if purpose == "generate" else "verify_escalated"
        with Timer() as t, span("rag.generate", model=model), tier_context(tier):
            answer = generate_answer(self.llm, query, chunks, hits,
                                     model=model, purpose=purpose)
        timings[gkey] = t.elapsed
        if self.s.retrieval.verify_faithfulness:
            # cheap model for verification
            with Timer() as t, span("rag.verify"), tier_context("plan"):
                answer = verify_answer(self.llm, query, answer, chunks, hits,
                                       model=self.s.llm.plan_model)
            timings[vkey] = t.elapsed
        return answer

    def _maybe_escalate_prose(self, query, chunks, hits, answer, gen_model, timings):
        """If enabled and the cheap pass deteriorated, re-generate on the next tier
        up and keep it only if it scores better. Returns (answer, escalations)."""
        if not self.escalation.enabled:
            return answer, []
        reason = self.escalation.prose_signal(answer)
        if reason is None:
            return answer, []
        hi = self.escalation.next_model(gen_model)
        if hi is None or not self.escalation.can_afford(0):
            return answer, []
        with span("rag.escalate", stage="generate", to_model=hi, reason=reason):
            hi_answer = self._generate_and_verify(
                query, chunks, hits, hi, timings, purpose="generate:escalated",
                tier=self._escalation_tier(hi))
        improved = prose_score(hi_answer) > prose_score(answer)
        log.info("prose escalation [%s]: %s -> %s (improved=%s)",
                 reason, gen_model, hi, improved)
        ev = escalation_event("generate", reason, gen_model, hi, improved)
        return (hi_answer if improved else answer), [ev]

    # ── tiered routing ───────────────────────────────────────────────────────

    def _generation_model(self, route: str) -> str:
        """Frontier model only for complex routes; cheap model otherwise."""
        if route in self.s.llm.complex_routes:
            return self.s.llm.complex_model
        return self.s.llm.answer_model

    def _escalation_tier(self, model: str) -> str:
        """The tier an escalated re-run on ``model`` runs under: ``complex`` when the
        ladder stepped up to the complex model, else ``escalation``."""
        return "complex" if model == self.s.llm.complex_model else "escalation"

    # ── relevance-floor gate ─────────────────────────────────────────────────

    def _below_floor(self, chunks: list[RetrievedChunk]) -> bool:
        floor = self.s.retrieval.min_rerank_score
        if not chunks:
            return True
        top = max((c.rerank_score if c.rerank_score is not None else c.score)
                  for c in chunks)
        return top < floor

    @staticmethod
    def _refusal(query: str) -> Answer:
        return Answer(
            summary="I couldn't find sufficiently relevant grounding in the corpus "
                    "to answer this confidently.",
            refused=True, unanswered=[query])


# resolve_inference_url now lives in retriever.remote_reranker (neutral, no heavy
# deps) so the indexing flow can share it without importing this module; re-exported
# here for the established `from pipelines.query_pipeline import resolve_inference_url`.
from retriever.remote_reranker import resolve_inference_url  # noqa: E402,F401


def build_retriever(settings: RagSettings | None = None, *, llm=None) -> Retriever:
    """Wire the production retriever (bge-m3 + Qdrant + bge-reranker). With an
    ``llm`` it uses the LLM planner; otherwise the heuristic (filter-only) one.

    The bge-m3 embedder and the cross-encoder reranker are served by the shared
    **RagInference** service (so ARag + MacroRAG don't each hold their own multi-GB
    copies) whenever a service URL resolves. The URL is the project **default**
    (``inference.remote_url`` in configs/rag.yaml -> ``http://localhost:8799``); the
    ``RAGINFER_URL`` env var **overrides** it (a URL to point elsewhere, or an empty
    value to force in-process models). If the service is unreachable and
    ``inference.fallback_local`` is on (default), the clients load the models
    in-process, so a stopped service degrades gracefully rather than failing."""
    from embeddings import select_embedder
    from retriever.rerank import BGEReranker, IdentityReranker
    from vectorstores import build_qdrant_store

    settings = settings or load_settings()

    remote_url = resolve_inference_url(settings)
    remote = bool(remote_url)
    fallback = settings.inference.fallback_local

    # Embedder: remote (RagInference) when a URL resolves, else in-process bge-m3 —
    # shared with the indexing flow via select_embedder.
    embedder = select_embedder(settings)

    qsettings = settings.qdrant.model_copy()
    if settings.qdrant_path():
        qsettings.path = str(settings.qdrant_path())
    store = build_qdrant_store(qsettings, dense_dim=settings.embedding.dense_dim)

    reranker: Reranker
    if not settings.reranker.enabled:
        reranker = IdentityReranker()
    elif remote:
        from retriever.remote_reranker import RemoteReranker
        reranker = RemoteReranker(url=f"{remote_url}/rerank", fallback_local=fallback)
    else:
        reranker = BGEReranker(settings.reranker)
    analyzer = None
    if llm is not None:
        from llm.understanding import LLMAnalyzer
        analyzer = LLMAnalyzer(llm, settings.retrieval,
                               plan_model=settings.llm.plan_model)
    return Retriever(embedder, store, reranker, analyzer, settings.retrieval,
                     ranking=settings.ranking)


def build_pipeline(settings: RagSettings | None = None) -> QueryPipeline:
    """Wire the full production stack: retriever + LLM + time-series store +
    (for quantitative queries) the plan-execute agent."""
    from llm import build_llm

    settings = settings or load_settings()
    llm = build_llm(settings.llm)
    retriever = build_retriever(settings, llm=llm)
    timeseries = TimeseriesStore(settings.timeseries_path)

    agent = None
    if settings.retrieval.plan_execute:
        from agent import LLMPlanner, PlanExecuteAgent, PlanExecutor
        try:
            sources = timeseries.sources()
        except Exception:
            sources = []
        planner = LLMPlanner(llm, model=settings.llm.plan_model, sources=sources)
        agent = PlanExecuteAgent(planner, PlanExecutor(retriever, timeseries))

    return QueryPipeline(retriever, llm, timeseries, settings, agent=agent)
