"""LLM retry/backoff + circuit breaker (gap review §H).

Exercises the provider-agnostic resilience layer directly with fake exceptions —
no SDK, no network, no real sleeps (sleep/rand/clock are injected).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm.resilience import CircuitBreaker, LLMUnavailable, call_with_retry, is_retryable

_NOSLEEP = {"sleep": lambda _d: None, "rand": lambda: 0.0}


def _cfg(max_retries: int = 3):
    return SimpleNamespace(max_retries=max_retries, backoff_base_s=0.5, backoff_max_s=8.0)


class HTTPErr(Exception):
    """Fake provider error carrying an HTTP status, like the SDK errors."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status_code = status


# ── is_retryable classification ──────────────────────────────────────────────

@pytest.mark.parametrize("status,expected",
                         [(429, True), (500, True), (503, True), (400, False), (401, False)])
def test_is_retryable_by_status(status, expected):
    assert is_retryable(HTTPErr(status)) is expected


def test_is_retryable_by_class_name():
    class RequestTimeout(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    assert is_retryable(RequestTimeout())
    assert is_retryable(APIConnectionError())
    assert not is_retryable(ValueError("bad prompt"))


def test_is_retryable_from_nested_response_status():
    class Wrapped(Exception):
        response = SimpleNamespace(status_code=502)

    assert is_retryable(Wrapped())


# ── retry behaviour ──────────────────────────────────────────────────────────

def test_retries_then_succeeds():
    n = {"calls": 0}

    def flaky():
        n["calls"] += 1
        if n["calls"] < 3:
            raise HTTPErr(429)
        return "ok"

    br = CircuitBreaker()
    assert call_with_retry(flaky, cfg=_cfg(3), breaker=br, **_NOSLEEP) == "ok"
    assert n["calls"] == 3


def test_non_retryable_propagates_and_spares_breaker():
    br = CircuitBreaker(threshold=1)
    with pytest.raises(HTTPErr):
        call_with_retry(lambda: (_ for _ in ()).throw(HTTPErr(400)),
                        cfg=_cfg(3), breaker=br, **_NOSLEEP)
    # a 4xx is our bug, not provider unhealth -> breaker must not have tripped
    assert call_with_retry(lambda: "ok", cfg=_cfg(3), breaker=br, **_NOSLEEP) == "ok"


def test_exhausted_retries_raise_underlying_error():
    with pytest.raises(HTTPErr):
        call_with_retry(lambda: (_ for _ in ()).throw(HTTPErr(503)),
                        cfg=_cfg(2), breaker=CircuitBreaker(threshold=99), **_NOSLEEP)


# ── circuit breaker ──────────────────────────────────────────────────────────

def test_breaker_opens_after_threshold_and_fast_fails():
    br = CircuitBreaker(threshold=2, reset_s=30.0)
    cfg = _cfg(0)  # no retries: each call is one failed attempt
    for _ in range(2):
        with pytest.raises(HTTPErr):
            call_with_retry(lambda: (_ for _ in ()).throw(HTTPErr(503)),
                            cfg=cfg, breaker=br, **_NOSLEEP)
    # breaker now open -> reject without calling the provider
    with pytest.raises(LLMUnavailable):
        call_with_retry(lambda: "unreached", cfg=cfg, breaker=br, **_NOSLEEP)


def test_breaker_half_opens_after_cooldown():
    clock = {"t": 0.0}
    br = CircuitBreaker(threshold=1, reset_s=30.0, clock=lambda: clock["t"])
    cfg = _cfg(0)
    with pytest.raises(HTTPErr):
        call_with_retry(lambda: (_ for _ in ()).throw(HTTPErr(503)),
                        cfg=cfg, breaker=br, **_NOSLEEP)
    with pytest.raises(LLMUnavailable):  # still cooling down
        call_with_retry(lambda: "x", cfg=cfg, breaker=br, **_NOSLEEP)
    clock["t"] = 31.0                    # cool-down elapsed -> half-open trial allowed
    assert call_with_retry(lambda: "recovered", cfg=cfg, breaker=br, **_NOSLEEP) == "recovered"
