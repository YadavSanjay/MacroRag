"""The OpenAI client omits `temperature` when a tier sets a reasoning effort above `none`:
gpt-5.4 rejects a non-default temperature then (HTTP 400, seen live on gpt-5.4-mini at
effort `low`, 2026-10-21). The llm-output-budget spec tests leave temperature unchecked."""

from __future__ import annotations

import sys
import types

import pytest

from llm.usage import tier_context
from utils.config import LLMSettings, ModelProfile, TierOptions


def _fake_openai(calls: list[dict]) -> types.ModuleType:
    mod = types.ModuleType("openai")

    def create(**kwargs):
        calls.append(kwargs)
        usage = types.SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12,
                                      prompt_tokens_details=None,
                                      completion_tokens_details=None)
        choice = types.SimpleNamespace(message=types.SimpleNamespace(content="ok"),
                                       finish_reason="stop")
        return types.SimpleNamespace(choices=[choice], usage=usage, model="gpt-5.4-mini")

    class OpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create))

    mod.OpenAI = OpenAI
    return mod


@pytest.mark.parametrize(("effort", "sends_temperature"), [
    (None, True), ("none", True), ("low", False), ("high", False), ("max", False)])
def test_temperature_only_without_reasoning(monkeypatch, effort, sends_temperature):
    calls: list[dict] = []
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(calls))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("OPENAI_API_KEY_FILE", raising=False)
    monkeypatch.delenv("MACRORAG_USE_REPLACEMENT_MODELS", raising=False)
    from llm.openai_client import OpenAIClient

    profile = ModelProfile(plan="gpt-5.4-mini", answer="gpt-5.4-nano", complex="gpt-5.4",
                           tier_options={"plan": TierOptions(effort=effort)})
    client = OpenAIClient(LLMSettings(provider="openai", profiles={"openai": profile}))
    with tier_context("plan"):
        client.complete(system="s", user="u", model="gpt-5.4-mini", purpose="t")
    assert ("temperature" in calls[0]) is sends_temperature
    assert calls[0].get("reasoning_effort") == effort
