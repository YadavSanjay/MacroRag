"""common.yaml takes operator-specific values (the User-Agent contact) from the environment.

The committed config carries ``${VAR:-default}`` references; the loader expands them so no
personal address lives in the repo.
"""

from __future__ import annotations

import re

import pytest

from ingest.common import config
from ingest.common.config import expand_env


def test_set_variable_replaces_reference(monkeypatch):
    monkeypatch.setenv("MACRORAG_TEST_CONTACT", "ops@example.org")
    got = expand_env("UA (contact: ${MACRORAG_TEST_CONTACT:-unset})")
    assert got == "UA (contact: ops@example.org)"


@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_variable_takes_default(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MACRORAG_TEST_CONTACT", raising=False)
    else:
        monkeypatch.setenv("MACRORAG_TEST_CONTACT", value)
    assert expand_env("contact: ${MACRORAG_TEST_CONTACT:-unset}") == "contact: unset"


def test_reference_without_default_to_unset_variable_raises(monkeypatch):
    monkeypatch.delenv("MACRORAG_TEST_CONTACT", raising=False)
    with pytest.raises(KeyError, match="MACRORAG_TEST_CONTACT"):
        expand_env("${MACRORAG_TEST_CONTACT}")


def test_nested_structure_expanded_and_other_scalars_kept(monkeypatch):
    monkeypatch.setenv("MACRORAG_TEST_CONTACT", "x@example.org")
    tree = {"http": {"user_agent": "c=${MACRORAG_TEST_CONTACT}", "retry_statuses": [429, 500],
                     "timeout_seconds": 30.0},
            "tags": ["${MACRORAG_TEST_CONTACT}", True]}
    assert expand_env(tree) == {"http": {"user_agent": "c=x@example.org",
                                         "retry_statuses": [429, 500], "timeout_seconds": 30.0},
                                "tags": ["x@example.org", True]}


def test_committed_common_yaml_has_no_literal_address_and_uses_env(monkeypatch):
    monkeypatch.setenv("MACRORAG_CONTACT_EMAIL", "ops@example.org")
    config.load_common_settings.cache_clear()
    try:
        ua = config.load_common_settings().http.user_agent
    finally:
        config.load_common_settings.cache_clear()
    assert "contact: ops@example.org" in ua
    raw = (config.CONFIG_DIR / "common.yaml").read_text(encoding="utf-8")
    assert "${MACRORAG_CONTACT_EMAIL" in raw
    assert re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", raw) is None
