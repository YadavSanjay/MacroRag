"""Shared Redis connection helper for the §I cross-replica backends.

One place to connect + health-check (``PING``) so every shared-state backend
(cache, rate limiter, job lock, job store, semantic cache) degrades the same way:
if Redis is unreachable or the library is absent, ``connect`` returns ``None`` and
the caller falls back to its in-process implementation. Keeps a single-box deploy
Redis-free while a multi-replica deploy gets shared state.
"""

from __future__ import annotations

from typing import Optional

from .logging import get_logger

log = get_logger(__name__)


def connect(url: str, *, feature: str = "redis") -> object | None:
    """Return a live Redis client for ``url``, or ``None`` if unavailable.

    Never raises — a failed connect logs a warning and returns ``None`` so the
    caller falls back in-process. ``feature`` only labels the log line.
    """
    try:
        import redis  # optional dependency
    except Exception as exc:  # pragma: no cover - redis is a declared prod dep
        log.warning("%s: redis library not installed (%s); using in-process", feature, exc)
        return None
    try:
        client = redis.Redis.from_url(url)
        client.ping()
        return client
    except Exception as exc:
        log.warning("%s: redis unreachable at %s (%s); using in-process",
                    feature, url, exc)
        return None
