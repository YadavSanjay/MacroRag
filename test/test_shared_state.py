"""§I — cross-replica shared state on Redis (rate limiter, job lock, job store,
semantic cache). Exercised against the in-memory FakeRedis double: no network.

Two backends sharing one FakeRedis instance stand in for two API replicas.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest

from api.jobs import JobConflict, JobManager
from api.jobstore import NullJobStore, RedisJobStore, build_job_store
from utils.cache import InMemorySemanticCache, RedisSemanticCache, build_semantic_cache
from utils.config import RagSettings
from utils.fakeredis import FakeRedis
from utils.locks import InProcessLock, RedisLock, build_exclusive_lock
from utils.ratelimit import InMemoryRateLimiter, RedisRateLimiter, build_rate_limiter

# ── rate limiter ─────────────────────────────────────────────────────────────

def test_inmemory_rate_limiter_token_bucket():
    now = [0.0]
    rl = InMemoryRateLimiter(now=lambda: now[0])
    assert all(rl.allow("k", 3) for _ in range(3))
    assert rl.allow("k", 3) is False       # bucket empty
    now[0] += 60                            # full refill after a minute
    assert rl.allow("k", 3) is True


def test_redis_rate_limiter_cap_is_global_across_replicas():
    r = FakeRedis()
    now = [1000.0]
    a = RedisRateLimiter(r, now=lambda: now[0])   # replica A
    b = RedisRateLimiter(r, now=lambda: now[0])   # replica B, same Redis
    allowed = sum(int(lim.allow("key", 3)) for _ in range(5) for lim in (a, b))
    assert allowed == 3                     # global cap of 3, NOT 3 × replicas
    now[0] += 60                            # next fixed window resets
    assert a.allow("key", 3) is True


def test_build_rate_limiter_selects_backend():
    s = RagSettings()
    assert isinstance(build_rate_limiter(s), InMemoryRateLimiter)
    s.auth.rate_limit_backend = "redis"
    assert isinstance(build_rate_limiter(s, client=FakeRedis()), RedisRateLimiter)
    # redis requested but unavailable -> in-process fallback
    s.redis.url = "redis://127.0.0.1:6399/0"   # nothing listening
    assert isinstance(build_rate_limiter(s), InMemoryRateLimiter)


# ── distributed lock ─────────────────────────────────────────────────────────

def test_redis_lock_excludes_across_replicas():
    r = FakeRedis()
    a, b = RedisLock(r, ttl_s=100), RedisLock(r, ttl_s=100)
    assert a.acquire("corpus", "jobA") is True
    assert b.acquire("corpus", "jobB") is False   # held cross-replica
    assert b.holder("corpus") == "jobA"
    b.release("corpus", "jobB")                    # non-owner release is a no-op
    assert b.holder("corpus") == "jobA"
    a.release("corpus", "jobA")
    assert b.acquire("corpus", "jobB") is True


def test_build_exclusive_lock_selects_backend():
    s = RagSettings()
    assert isinstance(build_exclusive_lock(s), InProcessLock)
    s.jobs.backend = "redis"
    assert isinstance(build_exclusive_lock(s, client=FakeRedis()), RedisLock)


# ── job store ────────────────────────────────────────────────────────────────

def test_redis_job_store_roundtrip_recency_idempotency():
    s = RedisJobStore(FakeRedis())
    s.save({"id": "index-1", "type": "index", "status": "running"})
    s.save({"id": "index-2", "type": "index", "status": "succeeded"})
    assert s.load("index-1")["status"] == "running"
    assert [x["id"] for x in s.recent(10)] == ["index-2", "index-1"]  # newest first
    assert s.claim_idempotency("k", "index-1", 100) is None           # newly claimed
    assert s.claim_idempotency("k", "index-2", 100) == "index-1"      # already held


def test_build_job_store_selects_backend():
    s = RagSettings()
    assert isinstance(build_job_store(s), NullJobStore)
    s.jobs.backend = "redis"
    assert isinstance(build_job_store(s, client=FakeRedis()), RedisJobStore)


# ── JobManager over shared Redis (two "replicas") ────────────────────────────

def _redis_manager(shared):
    return JobManager(lock=RedisLock(shared, ttl_s=100), store=RedisJobStore(shared))


def test_exclusive_lock_holds_across_replicas():
    r = FakeRedis()
    a, b = _redis_manager(r), _redis_manager(r)
    gate = threading.Event()
    ja = a.submit("index", {}, lambda j: gate.wait(2.0) and {}, exclusive_group="corpus")
    with pytest.raises(JobConflict):                     # B blocked by A's redis lock
        b.submit("index", {}, lambda j: {}, exclusive_group="corpus")
    assert b.get_dict(ja.id)["type"] == "index"          # A's job visible on B via store
    gate.set()
    assert ja.wait(2.0) and ja.status == "succeeded"
    jb = b.submit("index", {}, lambda j: {}, exclusive_group="corpus")  # lock freed
    assert jb.wait(2.0) and jb.status == "succeeded"


def test_idempotency_dedups_on_same_replica():
    mgr = _redis_manager(FakeRedis())
    gate = threading.Event()
    j1 = mgr.submit("ingest", {}, lambda j: gate.wait(2.0) and {}, idempotency_key="req-1")
    j2 = mgr.submit("ingest", {}, lambda j: {}, idempotency_key="req-1")
    assert j2 is j1                                       # same job returned, not a new run
    gate.set()
    assert j1.wait(2.0)


def test_idempotency_conflicts_across_replicas():
    r = FakeRedis()
    a, b = _redis_manager(r), _redis_manager(r)
    gate = threading.Event()
    ja = a.submit("ingest", {}, lambda j: gate.wait(2.0) and {}, idempotency_key="req-9")
    with pytest.raises(JobConflict):
        b.submit("ingest", {}, lambda j: {}, idempotency_key="req-9")
    gate.set()
    assert ja.wait(2.0)


def test_job_records_survive_a_restart():
    r = FakeRedis()
    m1 = JobManager(store=RedisJobStore(r))
    j = m1.submit("eval", {}, lambda job: {"ok": 1})
    assert j.wait(2.0)
    m2 = JobManager(store=RedisJobStore(r))               # fresh manager, empty local memory
    rec = m2.get_dict(j.id)
    assert rec is not None and rec["status"] == "succeeded"
    assert j.id in [x["id"] for x in m2.list()]           # list merges persisted records


# ── semantic cache ───────────────────────────────────────────────────────────

def test_redis_semantic_cache_hit_miss_and_eviction():
    r = FakeRedis()
    c = RedisSemanticCache(r, threshold=0.9, max_entries=2)
    c.add("k1", np.array([1, 0, 0], dtype="float32"), {"a": 1})
    assert c.get(np.array([0.99, 0.01, 0.0])) == {"a": 1}   # near-identical hit
    assert c.get(np.array([0.0, 1.0, 0.0])) is None          # orthogonal miss
    c.add("k2", np.array([0, 1, 0], dtype="float32"), {"b": 2})
    c.add("k3", np.array([0, 0, 1], dtype="float32"), {"c": 3})   # evicts oldest (k1)
    assert r.get("macrorag:sem:val:k1") is None              # k1 dropped
    assert r.zcard("macrorag:sem:index") == 2
    assert c.get(np.array([0, 0, 1], dtype="float32")) == {"c": 3}


def test_redis_semantic_cache_shared_across_replicas():
    r = FakeRedis()
    a = RedisSemanticCache(r, threshold=0.9, max_entries=8)
    b = RedisSemanticCache(r, threshold=0.9, max_entries=8)   # different "replica"
    a.add("q1", np.array([1, 0, 0], dtype="float32"), {"answer": 42})
    assert b.get(np.array([1.0, 0.0, 0.0])) == {"answer": 42}  # B reuses A's entry


def test_build_semantic_cache_selection_and_fallback():
    s = RagSettings()
    assert build_semantic_cache(s) is None                    # disabled by default
    s.semantic_cache.enabled = True
    assert isinstance(build_semantic_cache(s), InMemorySemanticCache)
    s.semantic_cache.backend = "redis"
    assert isinstance(build_semantic_cache(s, client=FakeRedis()), RedisSemanticCache)
    s.redis.url = "redis://127.0.0.1:6399/0"                  # unavailable -> memory fallback
    assert isinstance(build_semantic_cache(s), InMemorySemanticCache)
