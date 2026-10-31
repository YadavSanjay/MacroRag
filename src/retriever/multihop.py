"""Iterative multi-hop retrieval: walk a causal chain one link at a time, each
hop's evidence seeding the next sub-question.

This differs from the single-level DECOMPOSE fan-out (llm.understanding): there the
sub-questions are proposed up front from the query alone and retrieved in parallel;
here each next sub-question is proposed FROM the evidence gathered so far, so the
chain "Taiwan conflict -> chip supply shock -> inflation -> Fed reaction -> rates"
is actually walked. Bounded (max_hops) and opt-in (off by default) — each hop is an
extra LLM call + a rerank pass, which is costly on a CPU box.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from llm.base import LLMClient
from llm.registry import get_registry
from llm.usage import prompt_context, tier_context
from utils.logging import get_logger
from utils.schema import MetaFilter, QueryAnalysis, RetrievedChunk

from .retriever import Retriever

log = get_logger(__name__)
# Cap the accumulated evidence handed downstream so multi-hop doesn't blow up the
# generator/synthesizer context — a small multiple of the per-hop final_k.
_BUDGET_MULT = 3


@dataclass
class MultiHopResult:
    chunks: list[RetrievedChunk]
    analysis: QueryAnalysis
    hops: list[str]              # sub-question walked at each hop (hops[0] = original)


def _brief(chunks: list[RetrievedChunk], *, limit: int = 6, chars: int = 200) -> str:
    lines = []
    for c in chunks[:limit]:
        tag = c.payload.get("publication") or c.payload.get("source_id") or ""
        lines.append(f"[{tag}] {c.text[:chars].strip()}")
    return "\n".join(lines) or "(none yet)"


def multi_hop_retrieve(retriever: Retriever, llm: LLMClient, query: str, *,
                       max_hops: int, base_filter: MetaFilter | None = None,
                       model: str | None = None) -> MultiHopResult:
    """Retrieve for ``query``, then iteratively propose+retrieve causal follow-on
    hops (up to ``max_hops`` total) until the planner says done or a hop surfaces
    no new evidence. Returns the deduped, score-ranked union."""
    max_hops = max(1, int(max_hops))
    acc: list[RetrievedChunk] = []
    seen: set[str] = set()
    hops: list[str] = []

    def _add(chunks: list[RetrievedChunk]) -> int:
        added = 0
        for c in chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                acc.append(c)
                added += 1
        return added

    first, analysis = retriever.retrieve(query, base_filter=base_filter)
    _add(first)
    hops.append(query)

    for hop in range(1, max_hops):
        nxt = _propose_next_hop(llm, query, hops, acc, model)
        if not nxt:
            break
        hop_chunks, _ = retriever.retrieve(nxt, base_filter=base_filter)
        if _add(hop_chunks) == 0:                 # converged — no new evidence
            log.info("multi-hop: hop %d ('%s') added nothing; stopping", hop + 1, nxt)
            break
        hops.append(nxt)

    analysis.route = "MULTI_HOP"
    acc.sort(key=lambda c: (c.rerank_score if c.rerank_score is not None else c.score),
             reverse=True)
    budget = max(retriever.s.final_k, retriever.s.final_k * _BUDGET_MULT)
    log.info("multi-hop walked %d hop(s), %d unique chunks (cap %d)",
             len(hops), len(acc), budget)
    return MultiHopResult(chunks=acc[:budget], analysis=analysis, hops=hops)


def _propose_next_hop(llm: LLMClient, original: str, hops: list[str],
                      evidence: list[RetrievedChunk],
                      model: str | None) -> str | None:
    try:
        rp = get_registry().resolve("next_hop", model=model)
        with prompt_context(rp.variant_id, rp.content_hash), tier_context("plan"):
            plan = llm.complete_json(
                system=rp.system, model=model, purpose="multi_hop",
                schema=rp.schema,
                user=rp.render(question=original,
                               hops="\n".join(f"- {h}" for h in hops),
                               evidence=_brief(evidence)))
    except Exception as exc:  # never let hop planning break retrieval
        log.warning("multi-hop next-hop planning failed (%s); stopping", exc)
        return None
    if plan.get("done"):
        return None
    nxt = str(plan.get("next_query", "")).strip()
    return nxt or None
