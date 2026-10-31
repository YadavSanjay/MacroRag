"""Hard spend ceilings on the base pipeline (Production Hardening §K).

Metering (`llm/usage.py`) is strong but the only *cap* was escalation-only
(`cost_budget_usd`); one caller could drive unbounded LLM cost within their RPM.
This adds daily spend ceilings — per-principal and global — enforced as admission
control: a request is admitted only if neither ceiling is already hit, and its
measured cost is recorded on completion. A per-request soft cap raises an alert
(not a block) when a single request runs unusually expensive.

A *principal* is the caller identity behind an API key (auth, rate limit, cost
attribution). It is not a data-isolation boundary — see `utils.schema.MetaFilter`
for the separate, dormant `tenant_id` hook that isolation would use.

Counters are keyed by date (so they roll over at midnight and self-expire) and
live in-process or in Redis (`backend`), shared across replicas via §I.
`build_cost_guard` returns a no-op guard when disabled.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Optional

from .logging import get_logger

log = get_logger(__name__)

_DAY_TTL_S = 172_800   # 2 days: yesterday's counter lingers briefly then self-cleans


class BudgetExceeded(RuntimeError):
    """Raised by :meth:`CostGuard.check` when a daily ceiling is already reached.
    Carries the scope/limit/spent so the API can return an informative 402."""

    def __init__(self, scope: str, limit: float, spent: float):
        self.scope = scope
        self.limit = limit
        self.spent = spent
        super().__init__(
            f"{scope} spend cap reached: ${spent:.4f} ≥ ${limit:.4f}")


# ── counter stores ───────────────────────────────────────────────────────────

class _MemoryStore:
    def __init__(self):
        self._d: dict[str, float] = {}

    def get(self, key: str) -> float:
        return self._d.get(key, 0.0)

    def add(self, key: str, amount: float) -> None:
        self._d[key] = self._d.get(key, 0.0) + amount


class _RedisStore:
    def __init__(self, client, *, ttl_s: int = _DAY_TTL_S):
        self._r = client
        self.ttl = ttl_s

    def get(self, key: str) -> float:
        try:
            raw = self._r.get(key)
            return float(raw) if raw else 0.0
        except Exception as exc:  # a read failure must not block requests
            log.warning("cost store read failed (%s); treating as $0", exc)
            return 0.0

    def add(self, key: str, amount: float) -> None:
        try:
            self._r.incrbyfloat(key, amount)
            self._r.expire(key, self.ttl)
        except Exception as exc:
            log.warning("cost store write failed (%s); spend not recorded", exc)


# ── guard ────────────────────────────────────────────────────────────────────

class CostGuard:
    """Enabled guard over a counter store. ``today`` is injectable for tests."""

    enabled = True

    def __init__(self, cost_settings, store, *, alerter=None,
                 today: Callable[[], str] = lambda: date.today().isoformat()):
        self.s = cost_settings
        self._store = store
        self._alerter = alerter
        self._today = today

    def _principal_key(self, principal: str | None) -> str:
        # The ``:tenant:`` namespace is deliberately kept in the key string even
        # though the concept is now called `principal`: renaming it would zero
        # every live daily counter at deploy time, letting callers spend past a
        # cap they had already reached for the rest of that day.
        return f"macrorag:cost:{self._today()}:tenant:{principal or 'anon'}"

    def _global_key(self) -> str:
        return f"macrorag:cost:{self._today()}:global"

    def check(self, principal: str | None) -> None:
        """Admission control: raise :class:`BudgetExceeded` if a daily ceiling is
        already reached. ``0`` ceilings are disabled."""
        if self.s.daily_usd > 0:
            spent = self._store.get(self._global_key())
            if spent >= self.s.daily_usd:
                raise BudgetExceeded("global daily", self.s.daily_usd, spent)
        if self.s.per_principal_daily_usd > 0:
            spent = self._store.get(self._principal_key(principal))
            if spent >= self.s.per_principal_daily_usd:
                raise BudgetExceeded("principal daily",
                                     self.s.per_principal_daily_usd, spent)

    def record(self, principal: str | None, cost_usd: float) -> None:
        """Fold a completed request's cost into the daily counters, and raise a
        per-request cost *alert* (not a block) when it exceeds ``per_request_usd``."""
        if cost_usd and cost_usd > 0:
            self._store.add(self._global_key(), cost_usd)
            self._store.add(self._principal_key(principal), cost_usd)
        if (self.s.per_request_usd > 0 and cost_usd > self.s.per_request_usd
                and self._alerter is not None):
            self._alerter.send(
                "request exceeded per-request cost cap",
                level="warning", principal=principal or "anon",
                cost=f"${cost_usd:.4f}", cap=f"${self.s.per_request_usd:.4f}")


class NullCostGuard:
    """Disabled — no ceilings. check/record are no-ops."""

    enabled = False

    def check(self, principal: str | None) -> None:
        return None

    def record(self, principal: str | None, cost_usd: float) -> None:
        return None


def build_cost_guard(settings, *, client: object | None = None, alerter=None):
    """Build the cost guard from a RagSettings. Falls back to in-memory counters if
    Redis is requested but unavailable. Returns a no-op guard when disabled."""
    if not settings.cost.enabled:
        return NullCostGuard()
    store: _MemoryStore | _RedisStore = _MemoryStore()
    if settings.cost.backend == "redis":
        from .redisconn import connect
        client = client or connect(settings.redis.url, feature="cost-guard")
        if client is not None:
            store = _RedisStore(client)
    return CostGuard(settings.cost, store, alerter=alerter)
