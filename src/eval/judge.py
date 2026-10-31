"""LLM-as-judge answer evaluation (ARag review §1.6 / §2.5.7). Scores a generated
answer on faithfulness (grounded in the retrieved context + numeric data) and
relevancy (addresses the question). Provider-agnostic via the LLMClient, so eval
runs on a cheap model and unit tests use the fake."""

from __future__ import annotations

from llm.base import LLMClient
from llm.generate import format_context, format_data
from llm.registry import get_registry
from llm.usage import prompt_context
from utils.logging import get_logger
from utils.schema import Answer, RetrievedChunk, TimeseriesHit

log = get_logger(__name__)


def _answer_text(answer: Answer) -> str:
    lines = [answer.summary]
    lines.extend(f"- {d.text}" for d in answer.details)
    return "\n".join(lines)


def judge_answer(llm: LLMClient, query: str, answer: Answer,
                 chunks: list[RetrievedChunk],
                 timeseries: list[TimeseriesHit] | None = None,
                 *, model: str | None = None) -> dict:
    rp = get_registry().resolve("judge", model=model)
    with prompt_context(rp.variant_id, rp.content_hash):
        parsed = llm.complete_json(system=rp.system, model=model, purpose="judge",
            schema=rp.schema,
            user=rp.render(
                question=query, answer=_answer_text(answer),
                context=format_context(chunks), data=format_data(timeseries or [])))

    def _score(key: str) -> float:
        try:
            return max(0.0, min(1.0, float(parsed.get(key, 0.0))))
        except (TypeError, ValueError):
            return 0.0

    return {"faithfulness": _score("faithfulness"),
            "relevancy": _score("relevancy"),
            "reason": str(parsed.get("reason", ""))}
