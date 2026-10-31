"""Scripted LLM client for unit tests — no network, deterministic responses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from .base import LLMClient
from .usage import record_call


class FakeLLM(LLMClient):
    """Returns canned responses.

    Pass a ``responder(system, user) -> str`` for full control, or ``responses``
    (a list consumed in call order). Useful for exercising plan/answer parsing.
    """

    def __init__(self, responder: Callable[[str, str], str] | None = None,
                 responses: list[str] | None = None, model: str = "fake"):
        self.model = model
        self._responder = responder
        self._responses = list(responses or [])
        self.calls: list[tuple[str, str]] = []
        self.models_used: list[str] = []   # per-call resolved model (tiered routing)

    def complete(self, *, system: str, user: str,
                 temperature: float | None = None, model: str | None = None,
                 purpose: str | None = None) -> str:
        self.calls.append((system, user))
        used_model = model or self.model
        self.models_used.append(used_model)
        if self._responder is not None:
            text = self._responder(system, user)
        elif self._responses:
            text = self._responses.pop(0)
        else:
            text = ""
        # Feed the usage/trace machinery so dev traces built on fakes still show
        # each call's prompt+response (token counts are 0 — fakes are free).
        record_call(provider="fake", model=used_model, purpose=purpose,
                    system=system, user=user, response=text)
        return text
