"""Exclusive locks for admin maintenance jobs (Production Hardening §I).

§H added a per-process guard so two concurrent index builds can't run against a
single Qdrant writer. That guard only covers one replica; :class:`RedisLock`
(``SET key holder NX EX ttl``) makes the exclusion hold **across replicas**, and
the TTL auto-releases the lock if the holding replica dies mid-job.
``build_exclusive_lock`` selects by config, falling back in-process.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Optional

from .logging import get_logger
from .redisconn import connect

log = get_logger(__name__)


class ExclusiveLock(ABC):
    @abstractmethod
    def acquire(self, group: str, holder: str) -> bool:
        """Try to take ``group`` for ``holder``; False if already held by another."""

    @abstractmethod
    def release(self, group: str, holder: str) -> None:
        """Release ``group`` iff ``holder`` owns it (idempotent, safe if not held)."""

    @abstractmethod
    def holder(self, group: str) -> str | None:
        """Current holder id, or None if free."""


class InProcessLock(ExclusiveLock):
    """Single-process exclusion (thread-safe). Equivalent to §H's original guard."""

    def __init__(self):
        self._held: dict[str, str] = {}
        self._lock = threading.Lock()

    def acquire(self, group: str, holder: str) -> bool:
        with self._lock:
            if self._held.get(group) is not None:
                return False
            self._held[group] = holder
            return True

    def release(self, group: str, holder: str) -> None:
        with self._lock:
            if self._held.get(group) == holder:
                self._held.pop(group, None)

    def holder(self, group: str) -> str | None:
        with self._lock:
            return self._held.get(group)


class RedisLock(ExclusiveLock):
    """Cross-replica exclusion via ``SET ... NX EX``. The TTL must exceed the
    longest exclusive job so a live holder never loses the lock, while a dead
    holder's lock still expires."""

    def __init__(self, client, *, ttl_s: int, prefix: str = "macrorag:joblock:"):
        self._r = client
        self.ttl = ttl_s
        self.prefix = prefix

    def _key(self, group: str) -> str:
        return f"{self.prefix}{group}"

    def acquire(self, group: str, holder: str) -> bool:
        try:
            return bool(self._r.set(self._key(group), holder, nx=True, ex=self.ttl))
        except Exception as exc:  # fail closed: if we can't confirm the lock, deny
            log.warning("redis lock acquire error (%s); denying to stay safe", exc)
            return False

    def release(self, group: str, holder: str) -> None:
        try:
            cur = self._r.get(self._key(group))
            cur = cur.decode() if isinstance(cur, bytes) else cur
            if cur is not None and cur == holder:
                self._r.delete(self._key(group))
        except Exception as exc:  # TTL will reclaim it anyway
            log.warning("redis lock release error (%s); TTL will reclaim", exc)

    def holder(self, group: str) -> str | None:
        try:
            cur = self._r.get(self._key(group))
            return cur.decode() if isinstance(cur, bytes) else cur
        except Exception:
            return None


def build_exclusive_lock(settings, *, client: object | None = None) -> ExclusiveLock:
    """Select the lock from ``settings`` (a RagSettings). ``client`` injects a Redis
    double for tests. Falls back in-process if Redis is unavailable."""
    if settings.jobs.backend == "redis":
        client = client or connect(settings.redis.url, feature="job-lock")
        if client is not None:
            return RedisLock(client, ttl_s=settings.jobs.lock_ttl_s)
    return InProcessLock()
