"""Fixtures for the llm-model-registry spec tests.

Puts the repo ``src`` on ``sys.path``, turns the repo dotenv file off before anything imports
``utils.config``, unsets ``MACRORAG_USE_REPLACEMENT_MODELS`` for every test (tests that need it
set it themselves) and clears the ``load_settings`` cache around each test. Reads only the
committed configs (``configs/rag.yaml``, ``configs/llm_pricing.yaml``); any YAML a test loads is
written under a temporary directory. The only fake is the ``anthropic`` SDK module (spec Notes,
C6), installed by the ``fake_anthropic`` fixture. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

# Before any import of utils.config: never load the repo's secrets dotenv file.
os.environ["MACRORAG_DOTENV"] = "0"

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

ENV_VAR = "MACRORAG_USE_REPLACEMENT_MODELS"


@pytest.fixture(autouse=True)
def _no_switch_env(monkeypatch):
    """Each test starts with the switch variable unset."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    yield


@pytest.fixture(autouse=True)
def _fresh_settings():
    """load_settings is lru_cached: clear it before and after each test."""
    from utils.config import load_settings

    load_settings.cache_clear()
    yield
    load_settings.cache_clear()


@pytest.fixture
def repo_root() -> Path:
    """The repository root (holds ``configs/``)."""
    return _ROOT


def make_fake_anthropic() -> types.ModuleType:
    """A stand-in for the ``anthropic`` SDK module (spec Notes, C6).

    ``Anthropic(**kwargs)`` accepts any keywords; its ``messages.create(**kwargs)`` returns a
    namespace with one text block and the usage counts held in the module's ``USAGE`` dict
    (set by the test before calling). Every ``create`` call's kwargs land in ``CALLS``.
    """
    mod = types.ModuleType("anthropic")
    mod.USAGE = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    mod.CALLS = []

    class _Messages:
        def create(self, **kwargs):
            mod.CALLS.append(kwargs)
            usage = types.SimpleNamespace(**mod.USAGE)
            block = types.SimpleNamespace(type="text", text="fake answer")
            return types.SimpleNamespace(content=[block], usage=usage)

    class Anthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.messages = _Messages()

    mod.Anthropic = Anthropic
    return mod


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Installs the fake SDK at ``sys.modules["anthropic"]`` and sets ``ANTHROPIC_API_KEY``."""
    mod = make_fake_anthropic()
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.delenv("ANTHROPIC_API_KEY_FILE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return mod
