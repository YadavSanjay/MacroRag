"""OpenAI chat-completions client (default provider; uses OPENAI_API_KEY)."""

from __future__ import annotations

import time

from utils.config import LLMSettings, read_secret, tier_options_for
from utils.logging import get_logger

from .base import LLMClient, LLMRefusalError, normalize_finish_reason
from .resilience import CircuitBreaker, call_with_retry
from .usage import current_tier, record_call

log = get_logger(__name__)

# The gpt-6 family accepts only the default temperature (1); any other value is an
# HTTP 400 (seen on gpt-6-sol and gpt-6-luna, 2026-10-17). The gpt-5.4 family accepts it.
_DEFAULT_TEMPERATURE_ONLY_PREFIXES = ("gpt-6",)


def accepts_temperature(model: str) -> bool:
    """Whether a chat-completions call to `model` may send a non-default temperature.

    Args:
        model: The OpenAI model id, possibly with a dated suffix.

    Returns:
        False for models that accept only the default temperature.
    """
    return not model.startswith(_DEFAULT_TEMPERATURE_ONLY_PREFIXES)


class OpenAIClient(LLMClient):
    def __init__(self, settings: LLMSettings | None = None):
        from openai import OpenAI

        self.settings = settings or LLMSettings()
        self.model = self.settings.model
        key = read_secret(self.settings.api_key_env)
        if not key:
            raise RuntimeError(f"{self.settings.api_key_env} not set in environment")
        res = self.settings.resilience
        # Explicit timeout, and max_retries=0 so OUR wrapper is the single source
        # of retry/backoff truth (the SDK otherwise retries 2× silently).
        self._client = OpenAI(api_key=key, timeout=res.request_timeout_s, max_retries=0)
        self._breaker = CircuitBreaker(threshold=res.breaker_threshold,
                                       reset_s=res.breaker_reset_s)
        self.last_usage: dict | None = None

    def complete(self, *, system: str, user: str,
                 temperature: float | None = None, model: str | None = None,
                 purpose: str | None = None) -> str:
        used_model = model or self.model
        # The calling tier's options (tier_context); unset ones keep the globals.
        opts = tier_options_for(self.settings, current_tier())
        create_kwargs: dict = dict(
            model=used_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            # GPT-5.x rejects `max_tokens`; `max_completion_tokens` is the modern
            # replacement and is accepted by gpt-4o models too. Reasoning tokens
            # count against it.
            max_completion_tokens=opts.max_tokens or self.settings.max_tokens,
        )
        # A reasoning effort above `none` makes gpt-5.4 reject a non-default temperature
        # (HTTP 400, seen on gpt-5.4-mini at effort `low`, 2026-10-21), so it is omitted then.
        reasons = opts.effort is not None and opts.effort != "none"
        if accepts_temperature(used_model) and not reasons:
            create_kwargs["temperature"] = (
                self.settings.temperature if temperature is None else temperature)
        if opts.effort is not None:
            create_kwargs["reasoning_effort"] = opts.effort
        if opts.timeout_s is not None:
            create_kwargs["timeout"] = opts.timeout_s
        t0 = time.perf_counter()
        resp = call_with_retry(
            lambda: self._client.chat.completions.create(**create_kwargs),
            cfg=self.settings.resilience, breaker=self._breaker, purpose=purpose)
        latency = time.perf_counter() - t0
        prompt = completion = cached = reasoning = 0
        if resp.usage:
            prompt = resp.usage.prompt_tokens
            completion = resp.usage.completion_tokens
            # automatic prompt caching reports the cached subset of the input
            details = getattr(resp.usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0
            out_details = getattr(resp.usage, "completion_tokens_details", None)
            reasoning = getattr(out_details, "reasoning_tokens", 0) or 0
            self.last_usage = {"prompt_tokens": prompt, "completion_tokens": completion,
                               "total_tokens": resp.usage.total_tokens,
                               "cached_tokens": cached}
        choice = resp.choices[0]
        finish = normalize_finish_reason("openai", getattr(choice, "finish_reason", None))
        self.last_finish_reason = finish
        text = choice.message.content or ""
        record_call(provider="openai", model=used_model, prompt_tokens=prompt,
                    completion_tokens=completion, cached_tokens=cached,
                    latency_s=latency, purpose=purpose,
                    system=system, user=user, response=text, finish_reason=finish,
                    reasoning_tokens=reasoning,
                    served_model=getattr(resp, "model", None))
        if finish == "refusal":   # billed above; complete_json fails soft on it
            raise LLMRefusalError(used_model, None)
        return text
