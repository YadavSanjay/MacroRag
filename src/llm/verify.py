"""Faithfulness verifier: a cheap post-generation pass that checks each answer
detail against the chunks it cites and drops the ungrounded ones. This is what
pushes faithfulness from ~0.7 to ~0.9 in production RAG (ARag review §2.4.7).

Fail-open: if the verifier returns nothing parseable, the answer is left intact
(a flaky verifier must never silently empty a good answer).
"""

from __future__ import annotations

from utils.logging import get_logger
from utils.schema import Answer, AnswerDetail, RetrievedChunk, TimeseriesHit

from .base import LLMClient
from .generate import format_data
from .registry import get_registry
from .usage import prompt_context

log = get_logger(__name__)


def verify_answer(llm: LLMClient, query: str, answer: Answer,
                  chunks: list[RetrievedChunk],
                  timeseries: list[TimeseriesHit] | None = None,
                  *, enabled: bool = True, model: str | None = None) -> Answer:
    if not enabled or answer.refused or not answer.details:
        return answer

    text_by_id = {c.chunk_id: c.text for c in chunks}
    cited_ids: list[str] = []
    claim_lines = []
    for i, d in enumerate(answer.details):
        cites = ", ".join(d.chunk_ids) if d.chunk_ids else "none"
        claim_lines.append(f"{i}. {d.text}  (cites: {cites})")
        cited_ids.extend(cid for cid in d.chunk_ids if cid not in cited_ids)
    context = "\n\n".join(f"[{cid}] {text_by_id.get(cid, '(missing)')}"
                          for cid in cited_ids) or "(no cited chunks)"
    # Numeric claims are grounded in the hydrated series, not the prose chunks,
    # so the verifier must see the DATA too or it wrongly drops correct figures.
    if timeseries:
        context += "\n\nDATA (numeric series provided to the analyst):\n" \
                   + format_data(timeseries)

    try:
        rp = get_registry().resolve("verify", model=model)
        with prompt_context(rp.variant_id, rp.content_hash):
            parsed = llm.complete_json(system=rp.system, model=model,
                                       user=rp.render(claims="\n".join(claim_lines),
                                                      context=context),
                                       schema=rp.schema, purpose="verify")
    except Exception as exc:  # never let verification break the answer
        log.warning("faithfulness verify failed (%s); keeping answer as-is", exc)
        return answer

    results = parsed.get("results") or []
    if not results:                      # fail-open on an unparseable verdict
        return answer
    supported = {int(r["index"]) for r in results
                 if "index" in r and r.get("supported")}

    kept: list[AnswerDetail] = []
    dropped: list[AnswerDetail] = []
    for i, d in enumerate(answer.details):
        (kept if i in supported else dropped).append(d)
    if dropped:
        log.info("verifier dropped %d/%d unsupported claim(s)",
                 len(dropped), len(answer.details))
    answer.details = kept
    answer.unsupported = [d.text for d in dropped]
    answer.used_chunk_ids = sorted({cid for d in kept for cid in d.chunk_ids}) \
        or answer.used_chunk_ids
    return answer
