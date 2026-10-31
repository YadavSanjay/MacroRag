"""Anthropic (Claude) client — alternate provider, selected via LLMSettings."""

from __future__ import annotations

import time

from utils.config import LLMSettings, read_secret, tier_options_for
from utils.logging import get_logger

from .base import LLMClient, LLMRefusalError, normalize_finish_reason
from .resilience import CircuitBreaker, call_with_retry
from .usage import current_tier, record_call

log = get_logger(__name__)

# Newer Anthropic models reject temperature/top_p/top_k (HTTP 400): Opus 4.7 and later
# and the whole Claude 5 family (Sonnet 5.5 and Haiku 5.5 reject non-default values).
# Older ones (haiku-4-5, sonnet-4-6, opus-4-6) still accept them. We omit the sampling
# param for the former so those profiles are callable. Prefix match covers point
# releases and dated suffixes. (Ref: claude-api skill, Thinking & Effort table.)
_NO_SAMPLING_PREFIXES = ("claude-opus-4-8", "claude-opus-4-7", "claude-opus-5",
                         "claude-sonnet-5", "claude-haiku-5", "claude-fable-5",
                         "claude-mythos-5")


def _accepts_temperature(model: str) -> bool:
    return not any(model.startswith(p) for p in _NO_SAMPLING_PREFIXES)


class AnthropicClient(LLMClient):
    def __init__(self, settings: LLMSettings | None = None):
        from anthropic import Anthropic

        # model + api_key_env are resolved from the anthropic profile by
        # LLMSettings' validator — no hardcoded model id here.
        self.settings = settings or LLMSettings(provider="anthropic")
        self.model = self.settings.model
        key = read_secret(self.settings.api_key_env)
        if not key:
            raise RuntimeError(f"{self.settings.api_key_env} not set in environment")
        res = self.settings.resilience
        # Explicit timeout, and max_retries=0 so OUR wrapper owns retry/backoff
        # (the SDK otherwise retries 2× silently).
        self._client = Anthropic(api_key=key, timeout=res.request_timeout_s, max_retries=0)
        self._breaker = CircuitBreaker(threshold=res.breaker_threshold,
                                       reset_s=res.breaker_reset_s)
        self.last_usage: dict | None = None

    def complete(self, *, system: str, user: str,
                 temperature: float | None = None, model: str | None = None,
                 purpose: str | None = None) -> str:
        # Prompt caching: mark the (identical-across-queries) system prompt as a
        # cache breakpoint so its tokens are billed/processed once (§2.2.2).
        system_arg: str | list[dict]
        if self.settings.prompt_cache:
            system_arg = [{"type": "text", "text": system,
                           "cache_control": {"type": "ephemeral"}}]
        else:
            system_arg = system
        used_model = model or self.model
        # The calling tier's options (tier_context); unset ones keep the globals.
        opts = tier_options_for(self.settings, current_tier())
        create_kwargs: dict = dict(
            model=used_model,
            system=system_arg,
            messages=[{"role": "user", "content": user}],
            # Thinking tokens count against max_tokens.
            max_tokens=opts.max_tokens or self.settings.max_tokens,
        )
        # Only send temperature to models that accept it (see _accepts_temperature).
        if _accepts_temperature(used_model):
            create_kwargs["temperature"] = (
                self.settings.temperature if temperature is None else temperature)
        if opts.effort is not None:
            create_kwargs["output_config"] = {"effort": opts.effort}
        if opts.timeout_s is not None:
            create_kwargs["timeout"] = opts.timeout_s
        t0 = time.perf_counter()
        resp = call_with_retry(
            lambda: self._client.messages.create(**create_kwargs),
            cfg=self.settings.resilience, breaker=self._breaker, purpose=purpose)
        latency = time.perf_counter() - t0
        prompt = completion = cached = written = 0
        u = getattr(resp, "usage", None)
        if u:
            # input_tokens excludes cached/created tokens, which are reported
            # separately; sum them for the true prompt size, and price the cache
            # read and write portions at their own rates (cache_read / cache_write).
            cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
            cache_creation = getattr(u, "cache_creation_input_tokens", 0) or 0
            prompt = u.input_tokens + cache_read + cache_creation
            completion = u.output_tokens
            cached = cache_read
            written = cache_creation
            self.last_usage = {"input_tokens": u.input_tokens,
                               "output_tokens": u.output_tokens,
                               "cache_read_input_tokens": cache_read,
                               "cache_creation_input_tokens": cache_creation}
        finish = normalize_finish_reason("anthropic", getattr(resp, "stop_reason", None))
        self.last_finish_reason = finish
        text = "".join(block.text for block in resp.content if block.type == "text")
        # Anthropic reports no separate thinking-token count (it is inside output_tokens).
        record_call(provider="anthropic", model=used_model, prompt_tokens=prompt,
                    completion_tokens=completion, cached_tokens=cached,
                    cache_write_tokens=written, latency_s=latency, purpose=purpose,
                    system=system, user=user, response=text, finish_reason=finish,
                    reasoning_tokens=0, served_model=getattr(resp, "model", None))
        if finish == "refusal":   # billed above; complete_json fails soft on it
            category = getattr(getattr(resp, "stop_details", None), "category", None)
            raise LLMRefusalError(used_model, category)
        return text
