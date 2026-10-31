"""Config loading: deployment env overrides win over rag.yaml / code defaults.

Guards §G of the gap review — Helm injects MACRORAG_QDRANT_URL / MACRORAG_OTEL_ENDPOINT
but config.py used to read env only for API-key secrets, so pods silently ignored their
configured endpoints. These tests pin the env > yaml > code-default precedence.
"""

from __future__ import annotations

from utils.config import RagSettings, _apply_env_overrides, load_settings

# ── _apply_env_overrides (pure overlay, no lru_cache in play) ────────────────

def test_qdrant_url_from_env(monkeypatch):
    monkeypatch.setenv("MACRORAG_QDRANT_URL", "http://qdrant:6333")
    cfg = _apply_env_overrides({})
    assert cfg["qdrant"]["url"] == "http://qdrant:6333"
    assert RagSettings(**cfg).qdrant.url == "http://qdrant:6333"


def test_otel_endpoint_env_implies_enable(monkeypatch):
    monkeypatch.setenv("MACRORAG_OTEL_ENDPOINT", "http://otel:4317")
    s = RagSettings(**_apply_env_overrides({}))
    assert s.observability.otel_endpoint == "http://otel:4317"
    assert s.observability.otel_enabled is True  # injecting an endpoint implies on


def test_explicit_yaml_otel_disabled_is_respected(monkeypatch):
    monkeypatch.setenv("MACRORAG_OTEL_ENDPOINT", "http://otel:4317")
    cfg = {"observability": {"otel_enabled": False}}
    s = RagSettings(**_apply_env_overrides(cfg))
    assert s.observability.otel_endpoint == "http://otel:4317"  # endpoint still applied
    assert s.observability.otel_enabled is False  # explicit yaml false wins over implicit


def test_otel_enabled_env_overrides_implicit(monkeypatch):
    monkeypatch.setenv("MACRORAG_OTEL_ENDPOINT", "http://otel:4317")
    monkeypatch.setenv("MACRORAG_OTEL_ENABLED", "false")
    s = RagSettings(**_apply_env_overrides({}))
    assert s.observability.otel_enabled is False  # explicit env wins over implicit enable


def test_env_wins_over_yaml_qdrant(monkeypatch):
    monkeypatch.setenv("MACRORAG_QDRANT_URL", "http://from-env:6333")
    cfg = {"qdrant": {"url": "http://from-yaml:6333"}}
    assert _apply_env_overrides(cfg)["qdrant"]["url"] == "http://from-env:6333"


def test_no_env_is_noop():
    # nothing set (conftest doesn't inject these) -> config unchanged
    assert _apply_env_overrides({}) == {}


# ── load_settings integration (bust the lru_cache around env changes) ────────

def test_load_settings_applies_env(monkeypatch):
    monkeypatch.setenv("MACRORAG_QDRANT_URL", "http://qdrant-live:6333")
    load_settings.cache_clear()
    try:
        s = load_settings()
        assert s.qdrant.url == "http://qdrant-live:6333"
    finally:
        load_settings.cache_clear()  # don't leak the env-derived settings to other tests


# ── principal rename (D3): the cost cap keeps its deprecated config alias ─────

def test_cost_cap_accepts_the_deprecated_per_tenant_key():
    # existing config files (and infra/config/*.yaml) must keep loading after the
    # tenant -> principal rename
    s = RagSettings(**{"cost": {"enabled": True, "per_tenant_daily_usd": 12.5}})
    assert s.cost.per_principal_daily_usd == 12.5


def test_cost_cap_prefers_the_new_key_name():
    s = RagSettings(**{"cost": {"per_principal_daily_usd": 3.0}})
    assert s.cost.per_principal_daily_usd == 3.0


def test_cost_cap_defaults_to_disabled():
    assert RagSettings().cost.per_principal_daily_usd == 0.0
