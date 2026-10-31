"""Answer generation: format retrieved chunks + numeric series into the answer
prompt, call the LLM, and parse the structured cited response into an Answer."""

from __future__ import annotations

from utils.logging import get_logger
from utils.schema import Answer, AnswerDetail, RetrievedChunk, TimeseriesHit

from .base import LLMClient
from .registry import get_registry
from .usage import prompt_context

log = get_logger(__name__)


def format_context(chunks: list[RetrievedChunk]) -> str:
    blocks = []
    for c in chunks:
        p = c.payload
        head = f"[{c.chunk_id}] {p.get('publisher','')} | {p.get('publication','')}" \
               f" | {p.get('release_date','n.d.')}"
        if p.get("section"):
            head += f" | {p['section']}"
        blocks.append(f"{head}\n{c.text}")
    if not blocks:
        return "(no context retrieved)"
    # §D: isolate external (adversarial-capable) chunk text as data, not instructions.
    from .sanitize import fence_untrusted
    return fence_untrusted("\n\n---\n\n".join(blocks), "CONTEXT")


def _fmt_num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:g}"


def format_data(hits: list[TimeseriesHit], *, max_groups: int = 40,
                max_points: int = 20) -> str:
    """Render hydrated numeric series for the prompt as compact, LABELLED paths —
    one line per (variable, scenario) with the value sequence and first/min/max/
    last stats. Grouping (not a global row cap) guarantees every variable a stress
    query needs survives, so the model can diff baseline vs adverse; the redundant
    per-row ``raw`` blob is dropped. This replaces the old raw-JSON dump whose
    40-row cap could truncate the relevant series out entirely."""
    if not hits:
        return "(no numeric series)"
    out = []
    for h in hits:
        groups: dict[tuple, list] = {}
        for r in h.rows:
            key = (r.get("variable") or "?", r.get("scenario") or "")
            groups.setdefault(key, []).append(r)
        lines = [f"{h.source} ({h.publisher}) — {len(h.rows)} obs, "
                 f"{len(groups)} series:"]
        for (var, scen), rows in list(groups.items())[:max_groups]:
            vals = [r.get("value") for r in rows if r.get("value") is not None]
            if not vals:
                continue
            label = var + (f" [{scen}]" if scen else "")
            nums = [_fmt_num(v) for v in vals]
            seq = (", ".join(nums) if len(nums) <= max_points
                   else ", ".join(nums[:max_points]) + f", … ({len(nums)} pts)")
            stats = (f"(first={_fmt_num(vals[0])} min={_fmt_num(min(vals))} "
                     f"max={_fmt_num(max(vals))} last={_fmt_num(vals[-1])})")
            lines.append(f"  {label}: {seq} {stats}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def generate_answer(llm: LLMClient, query: str, chunks: list[RetrievedChunk],
                    timeseries: list[TimeseriesHit] | None = None,
                    *, model: str | None = None, purpose: str = "generate") -> Answer:
    timeseries = timeseries or []
    rp = get_registry().resolve("answer", model=model)
    user = rp.render(question=query,
                     context=format_context(chunks), data=format_data(timeseries))
    with prompt_context(rp.variant_id, rp.content_hash):
        parsed = llm.complete_json(system=rp.system, user=user, model=model,
                                   schema=rp.schema, purpose=purpose)

    details = [AnswerDetail(text=str(d.get("text", "")),
                            chunk_ids=[str(x) for x in (d.get("chunk_ids") or [])])
               for d in (parsed.get("details") or [])]
    used = sorted({cid for d in details for cid in d.chunk_ids}) \
        or [c.chunk_id for c in chunks]
    return Answer(
        summary=str(parsed.get("summary", "")),
        details=details,
        unanswered=[str(u) for u in (parsed.get("unanswered") or [])],
        used_chunk_ids=used,
        timeseries=timeseries,
    )
