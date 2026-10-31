"""L1 exact-query cache (ARag review §2.2.1). Keyed on the normalized query +
facet filters + a version salt (answer-prompt + model), so a prompt or model
change transparently invalidates. In-process TTL+LRU by default; the same
interface fronts Redis in production.
"""

from __future__ import annotations

import hashlib
import re
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Optional

from .schema import MetaFilter

_WS = re.compile(r"\s+")


def normalize_query(query: str) -> str:
    return _WS.sub(" ", (query or "").strip().lower())


def cache_key(query: str, filt: MetaFilter | None, *, version: str = "") -> str:
    f = filt or MetaFilter()
    parts = [
        normalize_query(query),
        repr(sorted(f.factor_tags)), repr(sorted(f.entity_tags)),
        repr(sorted(f.tiers)), repr(sorted(f.doc_types)),
        repr(sorted(f.buckets)), repr(sorted(f.publishers)),
        str(f.date_from), str(f.date_to), version,
    ]
    if f.entity_exempt_factor_tags:            # appended only when set: old keys stay valid
        parts.append(repr(sorted(f.entity_exempt_factor_tags)))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class Cache(ABC):
    @abstractmethod
    def get(self, key: str) -> Any | None: ...

    @abstractmethod
    def set(self, key: str, value: Any) -> None: ...


class NullCache(Cache):
    """Disabled cache (always miss)."""

    def get(self, key): return None
    def set(self, key, value): return None


class InMemoryCache(Cache):
    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 2048):
        self.ttl = ttl_seconds
        self.max = max_entries
        self._d: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        item = self._d.get(key)
        if item is None:
            self.misses += 1
            return None
        ts, value = item
        if self.ttl and (time.time() - ts) > self.ttl:
            self._d.pop(key, None)
            self.misses += 1
            return None
        self._d.move_to_end(key)
        self.hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        self._d[key] = (time.time(), value)
        self._d.move_to_end(key)
        while len(self._d) > self.max:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()


class RedisCache(Cache):
    """Production L1 backend. Values are pickled; key namespace avoids clashes."""

    def __init__(self, url: str, ttl_seconds: int = 3600, prefix: str = "macrorag:"):
        import pickle

        import redis  # optional dependency
        self._pickle = pickle
        self._r = redis.Redis.from_url(url)
        self.ttl = ttl_seconds
        self.prefix = prefix

    def get(self, key: str):
        raw = self._r.get(self.prefix + key)
        return self._pickle.loads(raw) if raw else None

    def set(self, key: str, value) -> None:
        self._r.set(self.prefix + key, self._pickle.dumps(value),
                    ex=self.ttl or None)


_DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def build_cache(settings, *, shared_url: str | None = None) -> Cache:
    if not settings.enabled:
        return NullCache()
    if getattr(settings, "backend", "memory") == "redis":
        # Prefer the top-level redis.url (shared_url) unless cache.redis_url was set
        # to a non-default value explicitly (§I: single Redis source of truth).
        url = settings.redis_url
        if url == _DEFAULT_REDIS_URL and shared_url:
            url = shared_url
        try:
            return RedisCache(url, ttl_seconds=settings.ttl_seconds)
        except Exception as exc:  # fall back if redis unavailable
            from .logging import get_logger
            get_logger(__name__).warning("redis cache unavailable (%s); using memory", exc)
    return InMemoryCache(ttl_seconds=settings.ttl_seconds,
                         max_entries=settings.max_entries)


# ── L2 semantic cache ─────────────────────────────────────────────────────────


class SemanticCache(ABC):
    """Reuse a prior result when the query embedding is near-identical (cosine ≥
    threshold). Keyed on the dense query vector, not the exact string, so
    paraphrases hit. In-process (per-replica) or Redis-shared (§I)."""

    threshold: float

    # ``scope`` (spec cache-scope): an entry is only returned to a lookup with the
    # same scope — the caller passes its filter, cache version and corpus, so a
    # near-identical question under another filter or corpus never reuses it.
    @abstractmethod
    def get(self, query_vec, *, scope: str = "") -> Any | None: ...

    @abstractmethod
    def add(self, key: str, query_vec, value, *, scope: str = "") -> None: ...


class InMemorySemanticCache(SemanticCache):
    """Per-replica numpy store; bounded LRU. Fast, but each replica warms its own
    (a cold-start cost under horizontal scale, not a correctness issue)."""

    def __init__(self, threshold: float = 0.97, max_entries: int = 1024):
        import numpy as np
        self._np = np
        self.threshold = threshold
        self.max = max_entries
        self._keys: list[str] = []
        self._scopes: list[str] = []
        self._mat = None            # (n, d) normalised matrix
        self._values: list = []
        self.hits = 0
        self.misses = 0

    def _norm(self, vec):
        v = self._np.asarray(vec, dtype="float32")
        n = self._np.linalg.norm(v) or 1.0
        return v / n

    def get(self, query_vec, *, scope: str = ""):
        rows = [i for i, s in enumerate(self._scopes) if s == scope]
        if self._mat is None or not rows:
            self.misses += 1
            return None
        sims = self._mat[rows] @ self._norm(query_vec)
        j = int(sims.argmax())
        if float(sims[j]) >= self.threshold:
            self.hits += 1
            return self._values[rows[j]]
        self.misses += 1
        return None

    def add(self, key: str, query_vec, value, *, scope: str = "") -> None:
        row = self._norm(query_vec)[None, :]
        if self._mat is None:
            self._mat = row
        else:
            self._mat = self._np.vstack([self._mat, row])
        self._keys.append(key)
        self._scopes.append(scope)
        self._values.append(value)
        if len(self._values) > self.max:        # drop oldest (LRU-ish)
            # _mat was just assigned in both branches above; the intervening
            # method calls are what stop mypy carrying that narrowing forward.
            self._mat = self._mat[1:]   # type: ignore[index]
            self._keys.pop(0)
            self._scopes.pop(0)
            self._values.pop(0)


class RedisSemanticCache(SemanticCache):
    """Cross-replica shared vector cache (§I). Stores each entry's normalised
    float32 vector and pickled value in Redis, with a sorted-set index for recency
    + eviction. ``get`` batch-loads the (bounded, ≤ max_entries) vectors and picks
    the best cosine in numpy — a dependency-light equivalent to a RediSearch KNN
    index that stays correct and testable; for very large ``max_entries`` a
    RediSearch vector index is the upgrade path. Off by default, so the per-query
    batch load is an accepted trade for cross-replica reuse."""

    def __init__(self, client, *, threshold: float = 0.97, max_entries: int = 1024,
                 ttl_seconds: int = 3600, prefix: str = "macrorag:sem:"):
        import numpy as np
        self._np = np
        self._r = client
        self.threshold = threshold
        self.max = max_entries
        self.ttl = ttl_seconds
        self.prefix = prefix
        self._index = f"{prefix}index"
        self._seq = f"{prefix}seq"
        self.hits = 0
        self.misses = 0

    def _norm(self, vec):
        v = self._np.asarray(vec, dtype="float32")
        n = self._np.linalg.norm(v) or 1.0
        return (v / n).astype("float32")

    @staticmethod
    def _dec(v) -> str:
        return v.decode() if isinstance(v, bytes) else v

    def _scoped_index(self, scope: str) -> str:
        """One similarity index per scope (spec cache-scope); the unscoped index keeps
        its original key."""
        if not scope:
            return self._index
        import hashlib
        return f"{self._index}:{hashlib.sha256(scope.encode('utf-8')).hexdigest()[:16]}"

    def get(self, query_vec, *, scope: str = ""):
        try:
            index = self._scoped_index(scope)
            ids = [self._dec(i) for i in self._r.zrevrange(index, 0, -1)]
            if not ids:
                self.misses += 1
                return None
            raws = self._r.mget([f"{self.prefix}vec:{k}" for k in ids])
            q = self._norm(query_vec)
            best_sim, best_key = -1.0, None
            for k, raw in zip(ids, raws):
                if not raw:
                    continue
                v = self._np.frombuffer(raw, dtype="float32")
                sim = float(q @ v)
                if sim > best_sim:
                    best_sim, best_key = sim, k
            if best_key is not None and best_sim >= self.threshold:
                val = self._r.get(f"{self.prefix}val:{best_key}")
                if val:
                    import pickle
                    self.hits += 1
                    return pickle.loads(val)
        except Exception as exc:  # cache is best-effort; a miss is always safe
            from .logging import get_logger
            get_logger(__name__).warning("redis semantic cache get failed (%s)", exc)
        self.misses += 1
        return None

    def add(self, key: str, query_vec, value, *, scope: str = "") -> None:
        try:
            import pickle
            blob = pickle.dumps(value)
        except Exception as exc:  # unpicklable value -> just skip caching it
            from .logging import get_logger
            get_logger(__name__).warning("semantic cache: value not picklable (%s); skip", exc)
            return
        try:
            v = self._norm(query_vec).tobytes()
            self._r.set(f"{self.prefix}vec:{key}", v, ex=self.ttl)
            self._r.set(f"{self.prefix}val:{key}", blob, ex=self.ttl)
            score = self._r.incr(self._seq)
            index = self._scoped_index(scope)
            self._r.zadd(index, {key: score})
            ids = [self._dec(i) for i in self._r.zrevrange(index, 0, -1)]
            for stale in ids[self.max:]:            # evict beyond capacity (oldest)
                self._r.delete(f"{self.prefix}vec:{stale}", f"{self.prefix}val:{stale}")
                self._r.zrem(index, stale)
        except Exception as exc:
            from .logging import get_logger
            get_logger(__name__).warning("redis semantic cache add failed (%s)", exc)


def build_semantic_cache(settings, *, client: object | None = None) -> SemanticCache | None:
    """Build the L2 cache from a RagSettings, or None when disabled. Redis backend
    is shared across replicas; falls back to in-process if Redis is unavailable."""
    sc = settings.semantic_cache
    if not sc.enabled:
        return None
    if sc.backend == "redis":
        from .redisconn import connect
        client = client or connect(settings.redis.url, feature="semantic-cache")
        if client is not None:
            return RedisSemanticCache(client, threshold=sc.threshold,
                                      max_entries=sc.max_entries,
                                      ttl_seconds=settings.cache.ttl_seconds)
    return InMemorySemanticCache(sc.threshold, sc.max_entries)
