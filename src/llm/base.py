"""LLM client interface + robust JSON extraction.

Two providers (OpenAI default, Anthropic alternate) implement ``complete``; the
``complete_json`` helper wraps it with strict-JSON parsing tolerant of code
fences and surrounding prose, so the rest of the pipeline gets dicts.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from utils.logging import get_logger

log = get_logger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_REPAIR_TEMPLATE = (
    "\n\nYour previous reply did not satisfy the required JSON schema:\n{errors}\n"
    "Return ONLY the corrected JSON object — no prose, no code fences.")


def schema_errors(obj: Any, schema: dict | None) -> list[str]:
    """Validate ``obj`` against a JSON Schema; return human-readable error strings
    (empty list = valid). Returns ``[]`` (skips validation) if ``schema`` is None
    or ``jsonschema`` is unavailable — so structured output degrades to today's
    tolerant behaviour rather than failing."""
    if not schema:
        return []
    try:
        import jsonschema
    except Exception:  # pragma: no cover - jsonschema is a declared dep
        return []
    validator = jsonschema.Draft202012Validator(schema)
    return [e.message for e in validator.iter_errors(obj)]


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object from an LLM response."""
    text = (text or "").strip()
    if not text:
        return {}
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    log.warning("LLM response was not valid JSON (%d chars)", len(text))
    return {}


class LLMRefusalError(RuntimeError):
    """The provider refused the request (spec llm-output-budget).

    Attributes:
        model: The requested model.
        category: The provider's refusal category, or None when it gives none.
    """

    def __init__(self, model: str, category: str | None = None) -> None:
        super().__init__(f"{model} refused the request (category={category})")
        self.model = model
        self.category = category


# Provider stop/finish reasons -> the shared vocabulary; unmapped values pass through.
_FINISH_REASONS = {
    "anthropic": {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
                  "refusal": "refusal"},
    "openai": {"stop": "stop", "length": "length", "content_filter": "refusal"},
}


def normalize_finish_reason(provider: str, raw: str | None) -> str | None:
    """A provider's stop / finish reason in the shared vocabulary (spec llm-output-budget).

    Args:
        provider: ``openai`` or ``anthropic``.
        raw: The reason as the provider reported it, or None.

    Returns:
        ``stop``, ``length`` or ``refusal`` for the reasons the spec maps; any other value
        unchanged; None for None.
    """
    if raw is None:
        return None
    return _FINISH_REASONS.get(provider, {}).get(raw, raw)


class LLMClient(ABC):
    model: str
    # Normalized finish reason of the last ``complete`` (stop / length / refusal ...);
    # None when the client doesn't report one. complete_json reads it to spot cut-offs.
    last_finish_reason: str | None = None

    @abstractmethod
    def complete(self, *, system: str, user: str,
                 temperature: float | None = None,
                 model: str | None = None,
                 purpose: str | None = None) -> str:
        """Complete a prompt. ``model`` overrides the client default for this call
        (used by tiered routing — cheap model for plan/verify, frontier for
        complex generation). ``purpose`` is a free-text label (e.g. ``"generate"``,
        ``"verify"``) that tags the call's cost/usage log line for debugging."""

    def complete_json(self, *, system: str, user: str,
                      schema: dict | None = None,
                      temperature: float | None = None,
                      model: str | None = None,
                      purpose: str | None = None) -> dict[str, Any]:
        """Complete and parse a JSON object. When ``schema`` is given, validate the
        parse against it; on failure, make ONE repair retry (re-asking with the
        validation errors appended) and, if that still fails, fall back to the
        best-effort parse — i.e. today's fail-soft behaviour is preserved, but a
        silent ``{}`` becomes an observable, retried event. Provider-agnostic: no
        native JSON mode / tool use (see design doc §4).

        A refused reply (`LLMRefusalError`) fails soft: no repair follows it, the
        outcome is ``refused`` and the result is ``{}`` (or the first reply's parse
        when the repair was refused). A reply cut off at the output cap also records
        ``truncated``, once per call; its repair still runs."""
        from .schema_health import (
            FAILED,
            OK,
            REFUSED,
            REPAIRED,
            TRUNCATED,
            record_schema_result,
        )

        cut_off = False

        def call(prompt: str, label: str | None) -> str:
            nonlocal cut_off
            self.last_finish_reason = None
            try:
                return self.complete(system=system, user=prompt, temperature=temperature,
                                     model=model, purpose=label)
            finally:
                if self.last_finish_reason == "length":
                    cut_off = True

        def finish(outcome: str) -> None:
            record_schema_result(purpose, model, outcome)
            if cut_off:
                record_schema_result(purpose, model, TRUNCATED)

        try:
            obj = extract_json(call(user, purpose))
        except LLMRefusalError as exc:
            log.warning("LLM refused (purpose=%s, model=%s, category=%s); no repair",
                        purpose or "-", exc.model, exc.category)
            if schema is not None:
                finish(REFUSED)
            return {}
        if schema is None:
            return obj
        errors = schema_errors(obj, schema)
        if not errors:
            finish(OK)
            return obj
        log.warning("structured output failed schema (purpose=%s): %s; repairing",
                    purpose or "-", "; ".join(errors[:3]))
        repair_user = user + _REPAIR_TEMPLATE.format(errors="; ".join(errors[:5]))
        try:
            obj2 = extract_json(call(repair_user,
                                     f"{purpose}:repair" if purpose else "repair"))
        except LLMRefusalError as exc:
            log.warning("LLM refused the repair (purpose=%s, model=%s, category=%s); "
                        "using best-effort parse", purpose or "-", exc.model, exc.category)
            finish(REFUSED)
            return obj
        if not schema_errors(obj2, schema):
            finish(REPAIRED)
            return obj2
        log.warning("structured output still invalid after repair (purpose=%s); "
                    "using best-effort parse", purpose or "-")
        finish(FAILED)
        return obj
