"""Bounded retry + circuit breaker for provider LLM calls (Production Hardening §H).

Both provider clients route their single ``create()`` call through
:func:`call_with_retry`, so a transient provider **429 / 5xx / timeout / connection**
error is retried with exponential backoff + jitter instead of failing the whole
query — the single most likely production failure the docs name ("LLM 429"). When
the error says how long to wait (``retry-after-ms`` / ``retry-after``), that wait is
used instead, capped at ``backoff_max_s``.

A per-client :class:`CircuitBreaker` trips after a run of failed calls so we fail
fast (and stop hammering a degraded provider) rather than retrying every request
into a wall; it half-opens after a cool-down to probe recovery.

Retryability is detected **structurally** (an HTTP status attribute, or the
exception class name) so we never import the provider SDK's exception types —
keeping this module, like the rest of the harness, torch/SDK-free under test.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional, TypeVar

from utils.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")


class LLMUnavailable(RuntimeError):
    """Raised when the circuit breaker is open — the provider is deemed unhealthy
    and the call is rejected without hitting it. Distinct from the underlying
    provider error so callers can tell 'we chose not to try' from 'the try failed'."""


def is_retryable(exc: BaseException) -> bool:
    """True for transient provider failures worth retrying: HTTP **429** or **5xx**,
    timeouts, and connection errors. Detected without importing the SDK exception
    types: first an HTTP status attribute (``status_code`` / ``status`` /
    ``response.status_code``), else the exception class name."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    name = type(exc).__name__.lower()
    return any(k in name for k in ("timeout", "connection", "unavailable"))


@dataclass
class CircuitBreaker:
    """Fail-fast breaker: after ``threshold`` consecutive failed *calls* it opens
    for ``reset_s`` seconds (rejecting calls with :class:`LLMUnavailable`), then
    half-opens to let one trial through — success closes it, failure re-opens it.
    A single instance is shared across a client's calls; ``clock`` is injectable
    for tests."""

    threshold: int = 5
    reset_s: float = 30.0
    clock: Callable[[], float] = field(default=time.monotonic)
    _fails: int = 0
    _opened_at: float | None = None

    def before(self) -> None:
        """Gate a call: raise if the breaker is open and still cooling down."""
        if self._opened_at is None:
            return
        if self.clock() - self._opened_at < self.reset_s:
            raise LLMUnavailable(
                f"LLM circuit breaker open ({self._fails} consecutive failures); "
                f"cooling down (~{self.reset_s:.0f}s)")
        # cool-down elapsed → half-open: allow this one trial through.

    def on_success(self) -> None:
        self._fails = 0
        self._opened_at = None

    def on_failure(self) -> None:
        self._fails += 1
        if self._fails >= self.threshold:
            self._opened_at = self.clock()


def retry_after_s(exc: BaseException) -> float | None:
    """The wait a provider error asks for, in seconds, or None.

    Reads ``retry-after-ms`` (milliseconds), then ``retry-after`` (seconds), from
    ``exc.response.headers``; the first that is a finite number >= 0 wins. An HTTP-date
    ``retry-after`` is not used (spec llm-output-budget C9).
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            return value / scale
    return None


def call_with_retry(fn: Callable[[], T], *, cfg, breaker: CircuitBreaker,
                    purpose: str | None = None,
                    sleep: Callable[[float], None] = time.sleep,
                    rand: Callable[[], float] = random.random) -> T:
    """Invoke ``fn`` with bounded retry + breaker gating.

    ``cfg`` supplies ``max_retries`` (retries *after* the first attempt),
    ``backoff_base_s`` and ``backoff_max_s`` (full-jitter exponential backoff,
    capped). A **non-retryable** error propagates immediately and does *not* count
    toward the breaker (a 400/401 is our bug, not provider unhealth); only
    exhausted transient retries trip it. ``sleep`` / ``rand`` are injectable so
    tests run without real delays or nondeterminism.
    """
    breaker.before()
    attempt = 0
    while True:
        try:
            out = fn()
        except LLMUnavailable:
            raise
        except BaseException as exc:  # noqa: BLE001 - provider errors are opaque here
            if not is_retryable(exc):
                raise  # client-side / non-transient: don't retry, don't trip breaker
            if attempt < cfg.max_retries:
                asked = retry_after_s(exc)
                if asked is not None:   # the provider said how long to wait
                    delay = min(asked, cfg.backoff_max_s)
                else:
                    cap = min(cfg.backoff_max_s, cfg.backoff_base_s * (2 ** attempt))
                    delay = cap * rand()  # full jitter in [0, cap)
                log.warning(
                    "LLM call transient failure (purpose=%s, attempt %d/%d, %s): %s; "
                    "retrying in %.2fs", purpose or "-", attempt + 1,
                    cfg.max_retries + 1, type(exc).__name__, exc, delay)
                attempt += 1
                sleep(delay)
                continue
            breaker.on_failure()  # transient, but out of retries → provider unhealthy
            log.warning("LLM call failed after %d attempts (purpose=%s): %s",
                        cfg.max_retries + 1, purpose or "-", exc)
            raise
        else:
            breaker.on_success()
            return out
