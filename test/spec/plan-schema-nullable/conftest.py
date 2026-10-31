"""Fixtures for the plan-schema-nullable spec tests.

Puts the repo ``src`` on ``sys.path``, turns the repo dotenv file off before anything imports
``utils.config``, and clears the process-global schema-health aggregate around each test.

The plan schema is read through the real prompt registry
(``llm.registry.get_registry().resolve("plan").schema``), i.e. from the committed
``prompts/tasks/plan/schema.json``; nothing here patches the registry.

The only fake (spec Notes) is ``PlanReplier``, an ``llm.base.LLMClient`` test double standing
in for the provider: every ``complete`` call returns the same scripted plan reply and logs its
``purpose``. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import json
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

from llm.base import LLMClient  # noqa: E402  (needs src on sys.path, set just above)

PLAN_MODEL = "gpt-5.4-mini"     # one of the pilot's plan models (spec Requirement)
QUESTION = "how did treasury yields and credit spreads move after the 2022 hikes"


@pytest.fixture(autouse=True)
def _fresh_schema_health():
    """The schema-health aggregate is process-global: start and end each test empty."""
    from llm.schema_health import reset_schema_health

    reset_schema_health()
    yield
    reset_schema_health()


class PlanReplier(LLMClient):
    """Provider-boundary double: every ``complete`` returns ``json.dumps(reply)`` and appends
    its ``purpose`` to ``purposes`` (a repair call therefore returns the same reply)."""

    def __init__(self, reply: dict, model: str = PLAN_MODEL):
        self.model = model
        self.reply = reply
        self.purposes: list[str | None] = []
        self.last_finish_reason = None

    def complete(self, *, system: str, user: str, temperature: float | None = None,
                 model: str | None = None, purpose: str | None = None) -> str:
        self.purposes.append(purpose)
        self.last_finish_reason = "stop"
        return json.dumps(self.reply)


def plan_schema() -> dict:
    """The plan task's schema as the registry resolves it (spec C1)."""
    from llm.registry import get_registry

    schema = get_registry().resolve("plan").schema
    assert isinstance(schema, dict), schema
    return schema


@pytest.fixture(scope="session")
def psn():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(PLAN_MODEL=PLAN_MODEL, QUESTION=QUESTION,
                                 PlanReplier=PlanReplier, plan_schema=plan_schema)
