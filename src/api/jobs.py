"""In-process job manager for the admin maintenance surface (UI design doc §7).

Long-running maintenance actions (index build, eval, ingest, curation) run in a
background thread and report progress as a growing log + a terminal status, so
the UI can launch one and follow it over SSE. Deliberately in-memory and
single-process — adequate for the single-replica admin surface; a durable queue
is a later concern.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timezone
from typing import Any, Optional

from utils.locks import ExclusiveLock, InProcessLock
from utils.logging import get_logger

from .jobstore import JobStore, NullJobStore

log = get_logger(__name__)

# status values
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
_TERMINAL = {SUCCEEDED, FAILED, CANCELLED}


class JobCancelled(Exception):
    """Raised by a runner's cooperative ``job.check_cancel()`` when a cancel has
    been requested, so the job manager marks the job CANCELLED (not FAILED)."""


class JobConflict(Exception):
    """Raised by ``submit`` when an exclusive job is rejected because another job
    in the same ``exclusive_group`` is still running. Prevents e.g. two concurrent
    full index re-embeds against a single Qdrant writer (which the docs warn
    deadlocks). The API maps this to HTTP 409. See Production Hardening §H.

    NOTE: this guard is per-process. With multiple replicas, two pods could still
    each start an exclusive job — a cross-replica lock (Redis) is completed in §I.
    """


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Job:
    """A single background task with a thread-safe log and terminal result."""

    def __init__(self, job_id: str, type_: str, params: dict[str, Any]):
        self.id = job_id
        self.type = type_
        self.params = params
        self.status = QUEUED
        self.created_at = _now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.result: dict | None = None
        self.error: str | None = None
        self.exclusive_group: str | None = None    # set by JobManager.submit
        self.idempotency_key: str | None = None     # set by JobManager.submit
        self._logs: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._cancel = threading.Event()

    # ── cooperative cancellation ──────────────────────────────────────────────
    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def request_cancel(self) -> None:
        self._cancel.set()

    def check_cancel(self) -> None:
        """Runners call this at safe checkpoints (loop boundaries); it raises
        :class:`JobCancelled` when a cancel has been requested. Opaque single-call
        work that never checks is cancelled best-effort (only at pre-start)."""
        if self._cancel.is_set():
            raise JobCancelled()

    def log(self, msg: str) -> None:
        with self._lock:
            self._logs.append({"ts": _now(), "msg": str(msg)})

    def logs_since(self, index: int) -> list[dict[str, str]]:
        with self._lock:
            return self._logs[index:]

    @property
    def n_logs(self) -> int:
        with self._lock:
            return len(self._logs)

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def to_dict(self, *, with_logs: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }
        if with_logs:
            with self._lock:
                d["logs"] = list(self._logs)
        else:
            d["n_logs"] = self.n_logs
        return d


class JobManager:
    """Submits jobs to background threads and keeps a bounded history."""

    def __init__(self, max_history: int = 200, *,
                 lock: ExclusiveLock | None = None,
                 store: JobStore | None = None,
                 idempotency_ttl_s: int = 3600):
        self._jobs: dict[str, Job] = {}
        self._order: deque[str] = deque()
        self._max = max_history
        self._lock = threading.Lock()
        self._counter = 0
        # §I: exclusive-group lock (in-process by default; Redis for cross-replica)
        # and a durable/cross-replica record store (no-op by default).
        self._exlock = lock or InProcessLock()
        self._store = store or NullJobStore()
        self._idem_ttl = idempotency_ttl_s
        self._idem: dict[str, str] = {}   # idempotency_key -> job_id (local fast-path)

    def submit(self, type_: str, params: dict[str, Any],
               fn: Callable[[Job], dict | None],
               *, exclusive_group: str | None = None,
               idempotency_key: str | None = None) -> Job:
        """Launch ``fn`` on a background thread.

        ``exclusive_group`` serializes store-mutating maintenance (index / ingest /
        curate) so concurrent Qdrant writers can't deadlock — held cross-replica
        when a Redis lock is configured (§H/§I); a rejected submit raises
        :class:`JobConflict`.

        ``idempotency_key`` dedups repeated submissions: if a live/non-terminal job
        already holds the key, that job is returned (same replica) or a
        :class:`JobConflict` is raised (held on another replica).
        """
        with self._lock:
            self._counter += 1
            job_id = f"{type_}-{self._counter}"

            # Idempotency: atomically claim the key for this new job_id. A returned
            # owner id means someone already holds it.
            if idempotency_key is not None:
                owner = self._idem.get(idempotency_key)
                if owner is None:
                    owner = self._store.claim_idempotency(
                        idempotency_key, job_id, self._idem_ttl)
                if owner is not None:
                    live = self._jobs.get(owner)
                    if live is not None:
                        return live   # same replica: return the in-flight job (or its cached result)
                    rec = self._store.load(owner)
                    if rec is not None and rec.get("status") not in _TERMINAL:
                        raise JobConflict(
                            f"job {owner} is already running for this request "
                            "(idempotency key held on another replica)")
                    # else: prior holder is stale/terminal elsewhere → run anew
                else:
                    self._idem[idempotency_key] = job_id

            if exclusive_group is not None and not self._exlock.acquire(exclusive_group, job_id):
                raise JobConflict(
                    f"a '{exclusive_group}' job ({self._exlock.holder(exclusive_group)}) "
                    "is already running; wait for it to finish before starting another")

            job = Job(job_id, type_, params)
            job.exclusive_group = exclusive_group
            job.idempotency_key = idempotency_key
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._evict_locked()
        self._store.save(job.to_dict(with_logs=False))
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job

    def _evict_locked(self) -> None:
        while len(self._order) > self._max:
            old = self._order.popleft()
            self._jobs.pop(old, None)

    def _run(self, job: Job, fn: Callable[[Job], dict | None]) -> None:
        job.status = RUNNING
        job.started_at = _now()
        self._store.save(job.to_dict(with_logs=False))   # in-flight visible cross-replica
        try:
            job.check_cancel()               # cancelled before it really started
            job.result = fn(job) or {}
            job.status = SUCCEEDED
        except JobCancelled:                 # cooperative stop -> cancelled, not failed
            job.log("cancelled")
            job.status = CANCELLED
        except Exception as exc:  # any failure -> failed job, never crashes the server
            job.error = str(exc)
            job.log(f"ERROR: {exc}")
            job.status = FAILED
            log.warning("job %s failed: %s", job.id, exc)
        finally:
            job.finished_at = _now()
            if job.exclusive_group is not None:
                self._exlock.release(job.exclusive_group, job.id)
            self._store.save(job.to_dict(with_logs=True))   # durable terminal record
            job._done.set()

    def get(self, job_id: str) -> Job | None:
        """The *live* job object (owning replica only) — used for log streaming and
        cancellation. Cross-replica/persisted reads go through :meth:`get_dict`."""
        return self._jobs.get(job_id)

    def get_dict(self, job_id: str) -> dict[str, Any] | None:
        """Job snapshot for the detail endpoint: the live job if present, else the
        persisted record (so a job owned by another replica, or one from before a
        restart, is still visible)."""
        job = self._jobs.get(job_id)
        if job is not None:
            return job.to_dict()
        return self._store.load(job_id)

    def cancel(self, job_id: str) -> Job | None:
        """Request cancellation of a job. Returns the job (None if unknown). A
        non-terminal job is flagged; runners stop at their next checkpoint. A
        terminal job is returned unchanged (idempotent)."""
        job = self._jobs.get(job_id)
        if job is not None and not job.is_terminal:
            job.request_cancel()
        return job

    def list(self) -> list[dict[str, Any]]:
        """Most-recent-first summaries. Merges live jobs with persisted records
        (store), so jobs from other replicas / before a restart appear too. Live
        state wins on id collisions."""
        with self._lock:
            ids = list(reversed(self._order))
            local = {i: self._jobs[i].to_dict(with_logs=False)
                     for i in ids if i in self._jobs}
        # local recency first, then any store-only records (other replicas / pre-restart)
        ordered = [local[i] for i in ids if i in local]
        seen = set(local)
        for rec in self._store.recent(self._max):
            if rec["id"] not in seen:
                ordered.append(rec)
                seen.add(rec["id"])
        return ordered
