"""Minimal in-memory Redis double for tests (and as a dependency-free fallback).

Implements only the handful of commands the §I shared-state backends use
(rate limiter, distributed lock, job store, semantic cache). Returns ``bytes``
for string/zset reads, like real ``redis-py``, so the backends' decode paths are
exercised under test — keeping with the repo rule: no network in tests.

NOT a complete Redis; do not use in production.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Optional


def _b(v) -> bytes:
    return v if isinstance(v, bytes) else str(v).encode("utf-8")


class FakeRedis:
    def __init__(self, *, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._str: dict[bytes, bytes] = {}
        self._exp: dict[bytes, float] = {}          # key -> expiry epoch
        self._z: dict[bytes, dict[bytes, float]] = {}   # key -> {member: score}

    # ── expiry helpers ────────────────────────────────────────────────────────
    def _expired(self, key: bytes) -> bool:
        exp = self._exp.get(key)
        if exp is not None and self._clock() >= exp:
            self._str.pop(key, None)
            self._z.pop(key, None)
            self._exp.pop(key, None)
            return True
        return False

    # ── connection ────────────────────────────────────────────────────────────
    def ping(self) -> bool:
        return True

    # ── strings ───────────────────────────────────────────────────────────────
    def set(self, key, value, *, nx: bool = False,
            ex: int | None = None, px: int | None = None):
        key = _b(key)
        self._expired(key)
        if nx and key in self._str:
            return None
        self._str[key] = _b(value)
        if ex is not None:
            self._exp[key] = self._clock() + ex
        elif px is not None:
            self._exp[key] = self._clock() + px / 1000.0
        else:
            self._exp.pop(key, None)
        return True

    def get(self, key):
        key = _b(key)
        if self._expired(key):
            return None
        return self._str.get(key)

    def mget(self, keys) -> list:
        return [self.get(k) for k in keys]

    def delete(self, *keys) -> int:
        n = 0
        for key in keys:
            key = _b(key)
            if self._str.pop(key, None) is not None or self._z.pop(key, None) is not None:
                self._exp.pop(key, None)
                n += 1
        return n

    def exists(self, key) -> int:
        key = _b(key)
        return 0 if self._expired(key) else int(key in self._str or key in self._z)

    def incr(self, key, amount: int = 1) -> int:
        key = _b(key)
        self._expired(key)
        cur = int(self._str.get(key, b"0")) + amount
        self._str[key] = _b(cur)
        return cur

    def incrbyfloat(self, key, amount: float) -> float:
        key = _b(key)
        self._expired(key)
        cur = float(self._str.get(key, b"0")) + float(amount)
        self._str[key] = _b(repr(cur))
        return cur

    def expire(self, key, seconds: int) -> bool:
        key = _b(key)
        if self._expired(key) or (key not in self._str and key not in self._z):
            return False
        self._exp[key] = self._clock() + seconds
        return True

    # ── sorted sets (job ordering) ────────────────────────────────────────────
    def zadd(self, key, mapping: dict) -> int:
        key = _b(key)
        self._expired(key)
        z = self._z.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            member = _b(member)
            if member not in z:
                added += 1
            z[member] = float(score)
        return added

    def zrem(self, key, *members) -> int:
        key = _b(key)
        z = self._z.get(key, {})
        n = 0
        for m in members:
            if z.pop(_b(m), None) is not None:
                n += 1
        return n

    def zrevrange(self, key, start: int, end: int) -> list[bytes]:
        key = _b(key)
        if self._expired(key):
            return []
        z = self._z.get(key, {})
        ordered = [m for m, _ in sorted(z.items(), key=lambda kv: kv[1], reverse=True)]
        if end == -1:
            end = len(ordered) - 1
        return ordered[start:end + 1]

    def zcard(self, key) -> int:
        key = _b(key)
        return 0 if self._expired(key) else len(self._z.get(key, {}))
