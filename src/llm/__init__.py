"""LLM layer: provider clients, query planning, and cited answer generation."""

from __future__ import annotations

from utils.config import LLMSettings

from .base import LLMClient, extract_json
from .fake import FakeLLM
from .generate import format_context, format_data, generate_answer
from .pricing import cost_usd
from .prompts import render
from .understanding import LLMAnalyzer
from .usage import record_call, track_usage
from .verify import verify_answer

__all__ = [
    "LLMClient", "extract_json", "FakeLLM", "LLMAnalyzer", "verify_answer",
    "generate_answer", "format_context", "format_data", "render", "build_llm",
    "cost_usd", "record_call", "track_usage",
]


def build_llm(settings: LLMSettings | None = None) -> LLMClient:
    """Construct the configured provider client (deferred provider imports)."""
    settings = settings or LLMSettings()
    if settings.provider == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(settings)
    from .openai_client import OpenAIClient
    return OpenAIClient(settings)
