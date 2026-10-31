"""§K governance — alerting, cost caps, and the eval regression gate.

All network/HTTP is injected (fake poster / FakeRedis); no external calls.
"""

from __future__ import annotations

import pytest

from utils.alerting import LogAlerter, NullAlerter, WebhookAlerter, build_alerter
from utils.config import RagSettings
from utils.costguard import (
    BudgetExceeded,
    CostGuard,
    NullCostGuard,
    _MemoryStore,
    _RedisStore,
    build_cost_guard,
)
from utils.fakeredis import FakeRedis

# ── alerting ─────────────────────────────────────────────────────────────────

def test_build_alerter_selection():
    s = RagSettings()
    assert isinstance(build_alerter(s), NullAlerter)          # disabled
    s.alerting.enabled = True
    assert isinstance(build_alerter(s), LogAlerter)           # enabled, no webhook
    s.alerting.webhook_url = "https://hooks.example/T/B/x"
    assert isinstance(build_alerter(s), WebhookAlerter)


def test_webhook_alerter_posts_formatted_payload():
    sent = {}

    def poster(url, payload, timeout):
        sent.update(url=url, payload=payload, timeout=timeout)
        return True

    a = WebhookAlerter("https://hooks.example/x", timeout_s=3.0, poster=poster)
    assert a.send("eval regressed", level="critical", metrics="mrr") is True
    assert sent["url"] == "https://hooks.example/x"
    assert "eval regressed" in sent["payload"]["text"]
    assert "CRITICAL" in sent["payload"]["text"] and "metrics=mrr" in sent["payload"]["text"]


def test_webhook_alerter_swallows_failures():
    def boom(url, payload, timeout):
        raise RuntimeError("network down")

    assert WebhookAlerter("https://x", poster=boom).send("msg") is False   # no raise


# ── cost guard ───────────────────────────────────────────────────────────────

def _guard(store=None, *, principal_cap=0.0, daily_cap=0.0, per_req=0.0, alerter=None):
    s = RagSettings()
    s.cost.enabled = True
    s.cost.per_principal_daily_usd = principal_cap
    s.cost.daily_usd = daily_cap
    s.cost.per_request_usd = per_req
    return CostGuard(s.cost, store or _MemoryStore(), alerter=alerter,
                     today=lambda: "2026-07-14")


def test_null_cost_guard_is_noop():
    g = NullCostGuard()
    assert g.enabled is False
    g.check("t")            # never raises
    g.record("t", 100.0)    # never records


def test_principal_daily_cap_blocks_after_spend():
    g = _guard(principal_cap=1.0)
    g.check("acme")                      # fresh: allowed
    g.record("acme", 0.60)
    g.check("acme")                      # 0.60 < 1.0 still allowed
    g.record("acme", 0.50)               # now 1.10 ≥ 1.0
    with pytest.raises(BudgetExceeded) as ei:
        g.check("acme")
    assert ei.value.scope == "principal daily"
    g.check("other")                     # a different principal is unaffected


def test_global_daily_cap_blocks_all_principals():
    g = _guard(daily_cap=1.0)
    g.record("a", 0.7)
    g.record("b", 0.4)                   # global 1.1 ≥ 1.0
    with pytest.raises(BudgetExceeded) as ei:
        g.check("c")                     # even a never-seen principal is blocked
    assert ei.value.scope == "global daily"


def test_per_request_cost_triggers_alert_not_block():
    sent = []
    alerter = type("A", (), {"send": lambda self, m, **k: sent.append((m, k))})()
    g = _guard(per_req=0.10, alerter=alerter)
    g.record("acme", 0.25)               # over the per-request cap
    assert sent and "per-request cost cap" in sent[0][0]
    g.check("acme")                      # per-request overage does NOT block


def test_day_rollover_resets_counters():
    s = RagSettings()
    s.cost.enabled = True
    s.cost.per_principal_daily_usd = 1.0
    day = ["2026-07-14"]
    g = CostGuard(s.cost, _MemoryStore(), today=lambda: day[0])
    g.record("acme", 2.0)
    with pytest.raises(BudgetExceeded):
        g.check("acme")
    day[0] = "2026-07-15"                # new day -> new key -> clean slate
    g.check("acme")


def test_cost_guard_redis_backend_shares_counters():
    r = FakeRedis()
    a = _guard(_RedisStore(r), principal_cap=1.0)     # replica A
    b = _guard(_RedisStore(r), principal_cap=1.0)     # replica B, same Redis
    a.record("acme", 0.8)
    b.record("acme", 0.5)                          # shared: 1.3 ≥ 1.0
    with pytest.raises(BudgetExceeded):
        a.check("acme")                            # A sees B's spend


def test_build_cost_guard_selection_and_fallback():
    s = RagSettings()
    assert isinstance(build_cost_guard(s), NullCostGuard)        # disabled
    s.cost.enabled = True
    assert isinstance(build_cost_guard(s), CostGuard)            # memory
    s.cost.backend = "redis"
    g = build_cost_guard(s, client=FakeRedis())
    assert isinstance(g, CostGuard) and isinstance(g._store, _RedisStore)


# ── API 402 enforcement ──────────────────────────────────────────────────────

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402


def test_query_returns_402_when_over_budget():
    s = RagSettings()
    s.cost.enabled = True
    s.cost.per_principal_daily_usd = 1.0
    guard = build_cost_guard(s)          # memory guard
    guard.record(None, 5.0)              # pre-exhaust the anon principal's daily budget
    app = create_app(lambda: object(), s, cost_guard=guard)   # pipeline never reached
    c = TestClient(app)
    r = c.post("/query", json={"question": "anything"})
    assert r.status_code == 402
    assert "cap reached" in r.json()["detail"]


# ── eval regression gate (admin eval job) ────────────────────────────────────

import types  # noqa: E402

from api import app as appmod  # noqa: E402
from api.jobs import JobManager  # noqa: E402


def _wire_eval(monkeypatch, tmp_path, *, baseline_mrr, current_mrr):
    import eval.drift as drift
    import eval.harness as harness
    monkeypatch.setattr(harness, "load_golden", lambda p: [1, 2, 3])
    monkeypatch.setattr(
        harness, "evaluate_retrieval",
        lambda retr, golden, k=8, should_cancel=None: {"summary": {"mrr": current_mrr},
                                                       "rows": []})
    monkeypatch.setattr(drift, "load_baseline", lambda p: {"mrr": baseline_mrr})
    sent = []
    monkeypatch.setattr(appmod, "build_alerter",
                        lambda s: types.SimpleNamespace(send=lambda m, **k: sent.append(m)))
    return sent


def test_eval_job_fails_and_alerts_on_regression(monkeypatch, tmp_path):
    sent = _wire_eval(monkeypatch, tmp_path, baseline_mrr=0.9, current_mrr=0.5)
    s = RagSettings()
    s.eval.fail_on_regression = True
    s.alerting.enabled = True
    s.paths.eval_history = str(tmp_path / "hist.jsonl")
    runner = appmod._default_eval_runner(s, lambda: types.SimpleNamespace(retriever=None))
    job = JobManager().submit("eval", {"k": 8}, runner)
    assert job.wait(2.0)
    assert job.status == "failed"                 # gate enforced (fail_on_regression)
    assert sent and "regression" in sent[0]        # alert fired


def test_eval_job_flags_but_passes_without_fail_flag(monkeypatch, tmp_path):
    sent = _wire_eval(monkeypatch, tmp_path, baseline_mrr=0.9, current_mrr=0.5)
    s = RagSettings()
    s.eval.fail_on_regression = False              # alert only, don't fail the job
    s.alerting.enabled = True
    s.paths.eval_history = str(tmp_path / "hist.jsonl")
    runner = appmod._default_eval_runner(s, lambda: types.SimpleNamespace(retriever=None))
    job = JobManager().submit("eval", {"k": 8}, runner)
    assert job.wait(2.0)
    assert job.status == "succeeded"
    assert job.result["regressed"] is True
    assert sent                                    # still alerted
