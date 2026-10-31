"""Phase 2: schema-validated complete_json — validate → one repair retry →
best-effort fallback. Provider-agnostic; exercised with the scripted FakeLLM."""

from __future__ import annotations

from llm.base import schema_errors
from llm.fake import FakeLLM
from llm.registry import get_registry

SCHEMA = {"type": "object", "required": ["x"],
          "properties": {"x": {"type": "integer"}}, "additionalProperties": True}


def test_schema_errors_helper():
    assert schema_errors({"x": 1}, SCHEMA) == []
    assert schema_errors({}, SCHEMA)          # missing required key
    assert schema_errors({"x": 1}, None) == []  # no schema -> skip


def test_valid_output_no_repair():
    llm = FakeLLM(responses=['{"x": 1}'])
    out = llm.complete_json(system="s", user="u", schema=SCHEMA, purpose="t")
    assert out == {"x": 1}
    assert len(llm.calls) == 1          # no repair


def test_invalid_then_repair_recovers():
    llm = FakeLLM(responses=['{"nope": true}', '{"x": 7}'])
    out = llm.complete_json(system="s", user="u", schema=SCHEMA, purpose="t")
    assert out == {"x": 7}
    assert len(llm.calls) == 2          # one repair retry
    # the repair call re-asked with the errors appended to the user prompt
    assert "did not satisfy the required JSON schema" in llm.calls[1][1]


def test_both_invalid_falls_back_to_best_effort():
    llm = FakeLLM(responses=['{"nope": 1}', '{"still": 2}'])
    out = llm.complete_json(system="s", user="u", schema=SCHEMA, purpose="t")
    assert out == {"nope": 1}           # first best-effort parse preserved
    assert len(llm.calls) == 2          # bounded to a single repair


def test_no_schema_is_todays_behaviour():
    llm = FakeLLM(responses=["garbage not json"])
    out = llm.complete_json(system="s", user="u", purpose="t")
    assert out == {}                    # tolerant scraper, no repair
    assert len(llm.calls) == 1


def test_registry_exposes_task_schemas():
    reg = get_registry()
    # tasks with a required primary key
    assert reg.resolve("answer").schema["required"] == ["summary"]
    assert reg.resolve("synthesize").schema["required"] == ["shock_vector"]
    assert reg.resolve("verify").schema["required"] == ["results"]
    assert reg.resolve("next_hop").schema["required"] == ["done"]
    # fail-soft trio: object-only, no required keys
    assert "required" not in reg.resolve("plan").schema
    assert reg.resolve("plan").schema["type"] == "object"


def test_repair_purpose_is_tagged_for_observability():
    # the repair call carries a ':repair' purpose so it's visible in the trace
    seen = {}

    class _Rec(FakeLLM):
        def complete(self, *, system, user, temperature=None, model=None, purpose=None):
            seen.setdefault("purposes", []).append(purpose)
            return super().complete(system=system, user=user, temperature=temperature,
                                    model=model, purpose=purpose)

    llm = _Rec(responses=['{"bad": 1}', '{"x": 1}'])
    llm.complete_json(system="s", user="u", schema=SCHEMA, purpose="generate")
    assert seen["purposes"] == ["generate", "generate:repair"]
