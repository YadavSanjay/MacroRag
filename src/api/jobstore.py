"""Durable, cross-replica job records (Production Hardening §I).

The in-process :class:`JobManager` keeps *live* ``Job`` objects (threads, logs,
SSE) for the replica that owns a job. A :class:`JobStore` mirrors each job's
serializable snapshot so records **survive a restart** and are **readable from any
replica**, and it backs **idempotency** (a repeated key won't launch a duplicate).

Default is :class:`NullJobStore` (no persistence — today's behaviour); a Redis
deploy uses :class:`RedisJobStore`. Live log streaming/cancel still only work on
the owning replica; other replicas surface the persisted snapshot.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Optional

from utils.logging import get_logger

log = get_logger(__name__)


class JobStore(ABC):
    @abstractmethod
    def save(self, record: dict) -> None:
        """Upsert a job snapshot (keyed by ``record['id']``)."""

    @abstractmethod
    def load(self, job_id: str) -> dict | None:
        """Return the persisted snapshot, or None."""

    @abstractmethod
    def recent(self, limit: int) -> list[dict]:
        """Most-recent-first summary snapshots (no logs)."""

    @abstractmethod
    def claim_idempotency(self, key: str, job_id: str, ttl_s: int) -> str | None:
        """Atomically claim ``key`` for ``job_id``. Returns None if newly claimed,
        else the job_id that already holds it (caller treats as a duplicate)."""


class NullJobStore(JobStore):
    """No persistence — the default. Keeps single-box behaviour unchanged."""

    def save(self, record: dict) -> None: return None
    def load(self, job_id: str) -> dict | None: return None
    def recent(self, limit: int) -> list[dict]: return []
    def claim_idempotency(self, key: str, job_id: str, ttl_s: int) -> str | None:
        return None


class RedisJobStore(JobStore):
    """Redis-backed store: one JSON string per job, a sorted-set index for recency,
    and NX idempotency keys."""

    def __init__(self, client, *, prefix: str = "macrorag:job:",
                 record_ttl_s: int = 604_800):
        self._r = client
        self.prefix = prefix
        self.ttl = record_ttl_s
        self._index = f"{prefix}index"
        self._seq = f"{prefix}seq"

    def _key(self, job_id: str) -> str:
        return f"{self.prefix}rec:{job_id}"

    def _idem_key(self, key: str) -> str:
        return f"{self.prefix}idem:{key}"

    def save(self, record: dict) -> None:
        try:
            job_id = record["id"]
            self._r.set(self._key(job_id), json.dumps(record), ex=self.ttl)
            # score by a monotonic sequence so recency ordering is stable
            score = self._r.incr(self._seq)
            self._r.zadd(self._index, {job_id: score})
        except Exception as exc:  # persistence must never break job execution
            log.warning("job store save failed (%s)", exc)

    def load(self, job_id: str) -> dict | None:
        try:
            raw = self._r.get(self._key(job_id))
            return json.loads(raw) if raw else None
        except Exception as exc:
            log.warning("job store load failed (%s)", exc)
            return None

    def recent(self, limit: int) -> list[dict]:
        try:
            ids = self._r.zrevrange(self._index, 0, limit - 1)
            out = []
            for jid in ids:
                jid = jid.decode() if isinstance(jid, bytes) else jid
                rec = self.load(jid)
                if rec is not None:
                    rec.pop("logs", None)   # list view is summary-only
                    out.append(rec)
            return out
        except Exception as exc:
            log.warning("job store recent failed (%s)", exc)
            return []

    def claim_idempotency(self, key: str, job_id: str, ttl_s: int) -> str | None:
        try:
            ok = self._r.set(self._idem_key(key), job_id, nx=True, ex=ttl_s)
            if ok:
                return None
            cur = self._r.get(self._idem_key(key))
            return cur.decode() if isinstance(cur, bytes) else cur
        except Exception as exc:
            log.warning("job store idempotency claim failed (%s); not deduping", exc)
            return None


def build_job_store(settings, *, client: object | None = None) -> JobStore:
    """Select the store from ``settings`` (a RagSettings). ``client`` injects a
    Redis double for tests. Falls back to no-op persistence if Redis is unavailable."""
    if settings.jobs.backend == "redis":
        from utils.redisconn import connect
        client = client or connect(settings.redis.url, feature="job-store")
        if client is not None:
            return RedisJobStore(client, record_ttl_s=settings.jobs.record_ttl_s)
    return NullJobStore()
