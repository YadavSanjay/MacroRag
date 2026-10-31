"""LLM call logging + per-query cost aggregation.

Every provider client calls :func:`record_call` after a completion. That emits a
structured debug line — provider, model, purpose, token counts, latency, and an
estimated ``$`` cost (see :mod:`llm.pricing`) — and, when a :func:`track_usage`
context is active, folds the call into a running total that is logged when the
context exits. Both are built on ``contextvars`` so aggregation is correct across
the MCP server's worker threads and async handlers.

The pipeline wraps its top-level entry points (``answer`` / ``synthesize`` /
``discover_factors``) in ``track_usage(...)``, so each user-facing operation logs
one "LLM usage [op]: calls=N … cost=$X" summary covering every sub-call (plan,
HyDE, generate, verify, synthesize, …).
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

from utils.logging import get_logger

from .pricing import call_cost

log = get_logger("llm.calls")


@dataclass
class CallRecord:
    """One captured LLM call: its cost/token accounting plus (when prompt capture
    is enabled) the exact system+user prompt and raw response. Surfaced per-query
    in the trace so the Trace Explorer can drill into every model interaction."""
    purpose: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0
    system: str | None = None
    user: str | None = None
    response: str | None = None
    # Prompt provenance (from the registry, via prompt_context): which prompt
    # variant + content hash produced this call. Lets the trace attribute every
    # generation to an exact prompt version. None when the call wasn't wrapped.
    prompt_variant: str | None = None
    prompt_hash: str | None = None
    # Why the provider stopped (normalized: stop / length / refusal ...), the
    # reasoning tokens inside completion_tokens, and the model id the response
    # named. None / 0 when the client doesn't report them (spec llm-output-budget).
    finish_reason: str | None = None
    reasoning_tokens: int = 0
    served_model: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict:
        return {
            "purpose": self.purpose,
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "latency_s": round(self.latency_s, 4),
            "prompt_variant": self.prompt_variant,
            "prompt_hash": self.prompt_hash,
            "finish_reason": self.finish_reason,
            "reasoning_tokens": self.reasoning_tokens,
            "served_model": self.served_model,
            "system": self.system,
            "user": self.user,
            "response": self.response,
        }


@dataclass
class UsageAggregate:
    label: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    cost_complete: bool = True   # False if any call's model was unpriced
    latency_s: float = 0.0       # summed wall-clock across the block's calls
    call_log: list[CallRecord] = field(default_factory=list)


_current: contextvars.ContextVar[UsageAggregate | None] = contextvars.ContextVar(
    "llm_usage_aggregate", default=None)

# Prompt provenance for the call about to be made. A caller wraps its single
# ``complete``/``complete_json`` in ``prompt_context(variant, content_hash)`` so
# ``record_call`` can stamp the CallRecord without threading two params through
# every client method. (variant, hash) or None.
_prompt_ctx: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "llm_prompt_ctx", default=None)


@contextmanager
def prompt_context(variant: str | None, content_hash: str | None):
    """Tag the LLM call(s) made in this block with the resolved prompt's variant
    id + content hash, which ``record_call`` folds into the CallRecord/trace.
    Wrap exactly one ``complete``/``complete_json`` call so the tag is unambiguous."""
    if variant is None and content_hash is None:
        yield
        return
    token = _prompt_ctx.set((variant or "", content_hash or ""))
    try:
        yield
    finally:
        _prompt_ctx.reset(token)


# The model tier (plan / answer / complex / default / escalation) of the call about to
# be made, so a provider client can apply that tier's options (``TierOptions``)
# without a new ``complete`` parameter. None outside any ``tier_context``. Not read
# yet: spec llm-output-budget wires the clients to it.
_tier_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_tier_ctx", default=None)


@contextmanager
def tier_context(tier: str | None):
    """Mark the LLM call(s) made in this block as belonging to model tier ``tier``."""
    token = _tier_ctx.set(tier)
    try:
        yield
    finally:
        _tier_ctx.reset(token)


def current_tier() -> str | None:
    """The tier set by the innermost enclosing ``tier_context``, or None."""
    return _tier_ctx.get()


# ── prompt/response capture config ───────────────────────────────────────────
# Storing every prompt+response makes traces heavy, so capture is a toggle wired
# from TraceSettings at pipeline build. Token/cost accounting is always recorded;
# only the (potentially large) prompt+response strings are gated by this.
_capture_prompts = True
_capture_max_chars = 20_000


def configure_call_capture(*, enabled: bool, max_chars: int) -> None:
    """Set whether ``record_call`` stores prompt+response text (and the per-field
    truncation cap). Called once from the pipeline using ``TraceSettings``."""
    global _capture_prompts, _capture_max_chars
    _capture_prompts = bool(enabled)
    _capture_max_chars = max(0, int(max_chars))


def _truncate(text: str | None) -> str | None:
    if text is None:
        return None
    if _capture_max_chars and len(text) > _capture_max_chars:
        return text[:_capture_max_chars] + f"\n… [truncated {len(text) - _capture_max_chars} chars]"
    return text


def _is_same_model(requested: str, served: str) -> bool:
    """True when ``served`` is ``requested`` or a dated snapshot of it
    (``requested-<suffix starting with a digit>``, e.g. ``gpt-5.4-2026-03-05``)."""
    if served == requested:
        return True
    rest = served[len(requested) + 1:] if served.startswith(requested + "-") else ""
    return rest[:1].isdigit()


def record_call(*, provider: str, model: str, prompt_tokens: int = 0,
                completion_tokens: int = 0, cached_tokens: int = 0,
                cache_write_tokens: int = 0,
                latency_s: float = 0.0, purpose: str | None = None,
                system: str | None = None, user: str | None = None,
                response: str | None = None, finish_reason: str | None = None,
                reasoning_tokens: int = 0,
                served_model: str | None = None) -> float | None:
    """Log one LLM call and accumulate it into the active ``track_usage`` total
    (if any). ``system``/``user``/``response`` are the raw prompt+completion; they
    are captured into the per-query call log when prompt capture is enabled.
    ``cached_tokens`` and ``cache_write_tokens`` are the parts of ``prompt_tokens``
    read from and written to the provider's cache. ``finish_reason``,
    ``reasoning_tokens`` and ``served_model`` are stored on the CallRecord as given.
    Returns the estimated USD cost, or ``None`` when unpriced."""
    if served_model is not None and not _is_same_model(model, served_model):
        log.warning("LLM %s answered a request for %s (purpose=%s)",
                    served_model, model, purpose or "-")
    cost = call_cost(model, prompt_tokens=prompt_tokens,
                     completion_tokens=completion_tokens, cached_tokens=cached_tokens,
                     cache_write_tokens=cache_write_tokens)
    # §J: feed the Prometheus registry (calls/tokens/cost). Best-effort — metrics
    # must never break a completion.
    try:
        from utils.metrics import record_llm_call
        record_llm_call(provider, model, purpose, prompt_tokens, completion_tokens, cost)
    except Exception:  # pragma: no cover - defensive
        pass
    cost_str = f"${cost:.6f}" if cost is not None else "$? (unpriced)"
    cached_str = f" cached={cached_tokens}" if cached_tokens else ""
    log.info("LLM call provider=%s model=%s purpose=%s prompt=%d completion=%d%s "
             "latency=%.2fs cost=%s",
             provider, model, purpose or "-", prompt_tokens, completion_tokens,
             cached_str, latency_s, cost_str)

    agg = _current.get()
    if agg is not None:
        agg.calls += 1
        agg.prompt_tokens += prompt_tokens
        agg.completion_tokens += completion_tokens
        agg.cached_tokens += cached_tokens
        agg.latency_s += latency_s
        if cost is None:
            agg.cost_complete = False
        else:
            agg.cost_usd += cost
        pctx = _prompt_ctx.get()
        agg.call_log.append(CallRecord(
            purpose=purpose or "-", provider=provider, model=model,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cached_tokens=cached_tokens, cost_usd=cost, latency_s=latency_s,
            prompt_variant=pctx[0] if pctx else None,
            prompt_hash=pctx[1] if pctx else None,
            system=_truncate(system) if _capture_prompts else None,
            user=_truncate(user) if _capture_prompts else None,
            response=_truncate(response) if _capture_prompts else None,
            finish_reason=finish_reason, reasoning_tokens=reasoning_tokens,
            served_model=served_model,
        ))
    return cost


@contextmanager
def track_usage(label: str):
    """Aggregate every :func:`record_call` within this block and log one summary
    on exit. Re-entrant: nested ``track_usage`` calls fold into the outermost
    aggregate (so ``synthesize`` -> ``answer`` reports a single combined total)."""
    existing = _current.get()
    if existing is not None:
        # Already aggregating in an enclosing context; just join it.
        yield existing
        return

    agg = UsageAggregate(label=label)
    token = _current.set(agg)
    try:
        yield agg
    finally:
        _current.reset(token)
        total = agg.prompt_tokens + agg.completion_tokens
        cost_str = (f"${agg.cost_usd:.6f}" if agg.cost_complete
                    else f"~${agg.cost_usd:.6f}+ (some models unpriced)")
        log.info("LLM usage [%s]: calls=%d prompt=%d completion=%d cached=%d "
                 "total_tokens=%d cost=%s",
                 agg.label, agg.calls, agg.prompt_tokens, agg.completion_tokens,
                 agg.cached_tokens, total, cost_str)


def current_usage() -> UsageAggregate | None:
    """The active aggregate, if a ``track_usage`` context is open (else None)."""
    return _current.get()


def usage_snapshot() -> dict | None:
    """Serialise the active aggregate for a trace record: rolled-up token/cost
    totals plus the per-call log (each with its own tokens, cost and — when
    capture is on — prompt/response). Returns ``None`` outside a ``track_usage``
    context so the trace's ``usage`` field simply stays absent."""
    agg = _current.get()
    if agg is None:
        return None
    return {
        "calls": agg.calls,
        "prompt_tokens": agg.prompt_tokens,
        "completion_tokens": agg.completion_tokens,
        "cached_tokens": agg.cached_tokens,
        "total_tokens": agg.prompt_tokens + agg.completion_tokens,
        "cost_usd": round(agg.cost_usd, 6),
        "cost_complete": agg.cost_complete,
        "latency_s": round(agg.latency_s, 4),
        "call_log": [c.as_dict() for c in agg.call_log],
    }
