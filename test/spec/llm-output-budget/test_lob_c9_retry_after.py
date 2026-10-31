"""C9: call_with_retry honours retry-after-ms / retry-after, capped at backoff_max_s."""

from __future__ import annotations

import types

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

RAND = 0.5          # injected rand(): jitter is half the cap


class RateLimitedError(Exception):
    """A provider 429 shaped like the SDK errors: status_code + response.headers."""

    def __init__(self, headers: dict):
        super().__init__("429 Too Many Requests")
        self.status_code = 429
        self.response = types.SimpleNamespace(status_code=429, headers=headers)


def _sleeps(headers: dict, *, base=0.5, cap=30.0) -> list[float]:
    """One 429 with ``headers`` then success; returns what call_with_retry slept."""
    from llm.resilience import CircuitBreaker, call_with_retry
    from utils.config import ResilienceSettings

    cfg = ResilienceSettings(max_retries=3, backoff_base_s=base, backoff_max_s=cap)
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) == 1:
            raise RateLimitedError(headers)
        return "ok"

    slept: list[float] = []
    out = call_with_retry(fn, cfg=cfg, breaker=CircuitBreaker(), purpose="test",
                          sleep=slept.append, rand=lambda: RAND)
    assert out == "ok" and len(attempts) == 2
    return slept


def _jitter(base=0.5, cap=30.0, attempt=0) -> float:
    # oracle helper: today's delay min(backoff_max_s, backoff_base_s * 2**attempt) * rand()
    return min(cap, base * 2 ** attempt) * RAND


@pytest.mark.criterion("C9")
def test_retry_after_seconds_is_honoured_and_capped():
    # oracle: spec C9: retry-after 7 -> sleep 7; retry-after 120 with backoff_max_s 30 ->
    # sleep 30.
    # tolerance: abs=1e-9 s, a header value parsed to float
    assert _sleeps({"retry-after": "7"}) == [pytest.approx(7.0, abs=1e-9)]
    # tolerance: abs=1e-9 s, the cap itself
    assert _sleeps({"retry-after": "120"}) == [pytest.approx(30.0, abs=1e-9)]


@pytest.mark.criterion("C9")
def test_retry_after_ms_wins_over_seconds():
    # oracle: spec C9: retry-after-ms 2500 with retry-after 9 -> sleep 2500/1000 = 2.5.
    # tolerance: abs=1e-9 s, milliseconds divided by 1000
    assert _sleeps({"retry-after-ms": "2500", "retry-after": "9"}) == [
        pytest.approx(2.5, abs=1e-9)]


@pytest.mark.criterion("C9")
def test_unusable_values_fall_back_to_jitter():
    # oracle: spec C9: no header, an HTTP-date value or a negative value -> today's jittered
    # delay; hand calculation min(30, 0.5 * 2**0) * 0.5 = 0.25. Paired with a usable
    # header (retry-after 4 -> 4) so ignoring the header cannot pass.
    jit = _jitter()
    # tolerance: abs=1e-12 s, the same float arithmetic as the oracle
    assert jit == pytest.approx(0.25, abs=1e-12)
    for headers in ({}, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"},
                    {"retry-after": "-3"}, {"retry-after-ms": "-100"},
                    {"retry-after": "nan"}, {"retry-after": "inf"}):
        # tolerance: abs=1e-12 s, the same float arithmetic as the oracle
        assert _sleeps(headers) == [pytest.approx(jit, abs=1e-12)], headers
    # tolerance: abs=1e-9 s, a header value parsed to float
    assert _sleeps({"retry-after": "4"}) == [pytest.approx(4.0, abs=1e-9)]


@pytest.mark.criterion("C9")
def test_bad_ms_value_falls_back_to_seconds_or_jitter():
    # oracle: spec Requirement: a missing / unusable value falls back to the jittered
    # backoff. An unusable retry-after-ms alone -> jitter (0.25, hand calculation above);
    # a usable retry-after-ms alone -> its value (1500 ms -> 1.5 s).
    # tolerance: abs=1e-12 s, the same float arithmetic as the oracle
    assert _sleeps({"retry-after-ms": "soon"}) == [pytest.approx(0.25, abs=1e-12)]
    # tolerance: abs=1e-9 s, milliseconds divided by 1000
    assert _sleeps({"retry-after-ms": "1500"}) == [pytest.approx(1.5, abs=1e-9)]


@pytest.mark.criterion("C9")
@settings(max_examples=60, deadline=None)
@given(seconds=st.integers(1, 300), cap=st.integers(1, 60), ms=st.integers(1, 300_000),
       use_ms=st.booleans())
def test_property_sleep_is_the_header_capped(seconds, cap, ms, use_ms):
    # oracle: spec Requirement, computed in the test: the wait is the header value
    # (retry-after-ms / 1000 when present, else retry-after) capped at backoff_max_s.
    headers = {"retry-after": str(seconds)}
    want = float(seconds)
    if use_ms:
        headers["retry-after-ms"] = str(ms)
        want = ms / 1000
    # tolerance: abs=1e-9 s, a header value parsed to float and divided
    assert _sleeps(headers, cap=float(cap)) == [pytest.approx(min(want, cap), abs=1e-9)]
