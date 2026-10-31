"""Fixtures for the model-set-switch spec tests.

Puts the repo ``src`` on ``sys.path``, turns the repo dotenv file off before anything imports
``utils.config``, unsets ``MACRORAG_USE_REPLACEMENT_MODELS`` for every test (tests that need it
set it themselves) and clears the ``load_settings`` cache around each test. Reads only the
committed configs. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import os
import sys
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
