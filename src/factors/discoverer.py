"""LLM discovery of the macro risk factors relevant to a portfolio/theme.

One grounded, JSON-constrained generation over the retrieved evidence. Output is
validated against the controlled factor vocabulary and ranked by relevance; the
citation GATE (gate.py) is applied separately by the pipeline so every emitted
factor is backed by a dated chunk — same discipline as shock-vector synthesis.
"""

from __future__ import annotations

from llm.base import LLMClient
from llm.generate import format_context
from llm.registry import get_registry
from llm.usage import prompt_context
from utils.logging import get_logger
from utils.schema import RetrievedChunk

from .schema import RELEVANCE_ORDER, RelevantFactor
from .vocab import canonicalize, factor_reference, valid_entity_tags, valid_factor_tags

log = get_logger(__name__)


def discover_factors(llm: LLMClient, portfolio: str,
                     chunks: list[RetrievedChunk],
                     *, model: str | None = None,
                     top_k: int = 8) -> list[RelevantFactor]:
    if not chunks:
        return []
    rp = get_registry().resolve("discover_factors", model=model)
    with prompt_context(rp.variant_id, rp.content_hash):
        parsed = llm.complete_json(system=rp.system, model=model, purpose="discover_factors",
            schema=rp.schema,
            user=rp.render(
                portfolio=portfolio, context=format_context(chunks),
                factors=factor_reference(), top_k=top_k))

    raw = parsed.get("factors")
    if not isinstance(raw, list):
        return []

    allowed_factors = valid_factor_tags()
    allowed_entities = valid_entity_tags()
    out: list[RelevantFactor] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tag = canonicalize(item.get("factor_tag", ""))
        if tag not in allowed_factors or tag in seen:   # closed vocab; dedupe
            continue
        relevance = str(item.get("relevance", "medium")).lower()
        if relevance not in RELEVANCE_ORDER:
            relevance = "medium"
        entities = [canonicalize(e) for e in (item.get("entity_tags") or [])]
        entities = [e for e in dict.fromkeys(entities) if e in allowed_entities]
        chunk_ids = [str(c) for c in (item.get("chunk_ids") or []) if c]
        out.append(RelevantFactor(factor_tag=tag, relevance=relevance,
                                  entity_tags=entities, chunk_ids=chunk_ids,
                                  rationale=str(item.get("rationale", ""))))
        seen.add(tag)

    # Deterministic rank: high → medium → low, then keep the requested top_k.
    out.sort(key=lambda f: RELEVANCE_ORDER.index(f.relevance))
    return out[:top_k]
