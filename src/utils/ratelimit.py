"""Per-API-key rate limiting (Production Hardening §I).

The in-process token bucket enforces the limit **per replica**, so with
``replicaCount: N`` the effective cap is ``rpm × N`` — a real horizontal-scale
bug. :class:`RedisRateLimiter` moves the counter to Redis (a shared fixed-window)
so the cap is global across replicas. ``build_rate_limiter`` selects by config and
falls back to in-process if Redis is unreachable.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Optional

from .logging import get_logger
from .redisconn import connect

log = get_logger(__name__)


class RateLimiter(ABC):
    @abstractmethod
    def allow(self, key: str, rpm: int) -> bool:
        """Consume one unit for ``key``; return False when the per-minute cap is hit."""


class InMemoryRateLimiter(RateLimiter):
    """Token bucket, per process (today's behaviour). Smooth refill; bursts up to
    ``rpm``. Correct on a single replica; under-limits nothing but over-limits
    globally when replicated."""

    def __init__(self, *, now: Callable[[], float] = time.time):
        self._now = now
        self._buckets: dict[str, tuple[float, float]] = {}   # key -> (tokens, last_refill)

    def allow(self, key: str, rpm: int) -> bool:
        now = self._now()
        tokens, last = self._buckets.get(key, (float(rpm), now))
        tokens = min(rpm, tokens + (now - last) * (rpm / 60.0))   # refill
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1.0, now)
        return True


class RedisRateLimiter(RateLimiter):
    """Shared **fixed-window** counter: ``INCR`` a per-key, per-minute bucket and
    set a 60s expiry on first hit. The cap is global across replicas. Fixed windows
    can allow a short burst across a boundary (≤ 2× rpm briefly); that's an accepted
    trade for atomic, dependency-light global limiting."""

    def __init__(self, client, *, prefix: str = "macrorag:rl:",
                 now: Callable[[], float] = time.time):
        self._r = client
        self.prefix = prefix
        self._now = now

    def allow(self, key: str, rpm: int) -> bool:
        window = int(self._now() // 60)
        rkey = f"{self.prefix}{key}:{window}"
        try:
            count = self._r.incr(rkey)
            if count == 1:
                self._r.expire(rkey, 60)
            return count <= rpm
        except Exception as exc:  # never fail a request because Redis hiccupped
            log.warning("redis rate limiter error (%s); allowing request", exc)
            return True


def build_rate_limiter(settings, *, client: object | None = None) -> RateLimiter:
    """Select the limiter from ``settings`` (a RagSettings). ``client`` injects a
    Redis double for tests. Falls back to in-process if Redis is unavailable."""
    if settings.auth.rate_limit_backend == "redis":
        client = client or connect(settings.redis.url, feature="rate-limiter")
        if client is not None:
            return RedisRateLimiter(client)
    return InMemoryRateLimiter()
