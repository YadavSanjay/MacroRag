"""C2: configured_models lists every model id the LLM settings can reach, sorted and unique."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HC = [HealthCheck.function_scoped_fixture]
TIERS = ("plan", "answer", "complex", "default", "escalation")


def _settings(**kw):
    from utils.config import LLMSettings, ModelProfile

    def prof(d):
        return ModelProfile(**d)

    profiles = {p: prof(t) for p, t in kw.pop("profiles").items()}
    replacement = {p: prof(t) for p, t in kw.pop("replacement_profiles").items()}
    return LLMSettings(profiles=profiles, replacement_profiles=replacement, **kw)


# Hand-written profiles (fake ids: in-code settings are not completeness-checked, spec Notes).
PROFILES = {
    "openai": {"plan": "o-plan", "answer": "o-answer", "complex": "o-complex",
               "default": "o-default"},
    "anthropic": {"plan": "a-plan", "answer": "a-answer", "complex": "a-complex"},
}
REPLACEMENT = {
    "openai": {"plan": "ro-plan", "answer": "ro-answer", "complex": "ro-plan"},
    "anthropic": {"plan": "ra-plan", "answer": "ra-answer", "complex": "ra-complex",
                  "default": "ra-default"},
}


@pytest.mark.criterion("C2")
def test_hand_written_profiles_list():
    # oracle: spec Requirement "Configured" applied by hand. Active provider openai, switch off:
    # resolved model=o-default, plan=o-plan, answer=o-answer, complex=o-complex, no escalation.
    # Plus every tier of every profile and replacement profile; sorted, ro-plan once.
    from utils.config import configured_models

    llm = _settings(provider="openai", profiles=PROFILES, replacement_profiles=REPLACEMENT)
    assert configured_models(llm) == [
        "a-answer", "a-complex", "a-plan",
        "o-answer", "o-complex", "o-default", "o-plan",
        "ra-answer", "ra-complex", "ra-default", "ra-plan",
        "ro-answer", "ro-plan",
    ]


@pytest.mark.criterion("C2")
def test_flat_overrides_and_escalation_are_included():
    # oracle: spec Requirement "Configured" applied by hand. Provider anthropic with flat
    # answer_model and escalation_model set: resolved model=a-answer (no default -> answer),
    # plan=a-plan, answer=flat-answer, complex=a-complex, escalation=flat-esc.
    from utils.config import configured_models

    llm = _settings(provider="anthropic", profiles=PROFILES, replacement_profiles=REPLACEMENT,
                    answer_model="flat-answer", escalation_model="flat-esc")
    assert configured_models(llm) == [
        "a-answer", "a-complex", "a-plan",
        "flat-answer", "flat-esc",
        "o-answer", "o-complex", "o-default", "o-plan",
        "ra-answer", "ra-complex", "ra-default", "ra-plan",
        "ro-answer", "ro-plan",
    ]


@pytest.mark.criterion("C2")
def test_replacement_switch_resolves_from_replacement_profile():
    # oracle: spec Requirement "Configured" by hand. Switch on, provider openai: resolved
    # tiers come from the openai replacement profile (model = answer, no default), all of
    # which are already listed, so the list is the same set as with the switch off.
    from utils.config import configured_models

    llm = _settings(provider="openai", profiles=PROFILES, replacement_profiles=REPLACEMENT,
                    use_replacement_models=True)
    assert llm.answer_model == "ro-answer"
    assert configured_models(llm) == [
        "a-answer", "a-complex", "a-plan",
        "o-answer", "o-complex", "o-default", "o-plan",
        "ra-answer", "ra-complex", "ra-default", "ra-plan",
        "ro-answer", "ro-plan",
    ]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=40, deadline=None, suppress_health_check=_HC)
@given(which=st.sampled_from(["profiles", "replacement_profiles"]),
       provider=st.sampled_from(["openai", "anthropic"]),
       active=st.sampled_from(["openai", "anthropic"]),
       esc=st.from_regex(r"zesc-[a-z0-9]{1,8}", fullmatch=True))
def test_adding_an_escalation_adds_that_id(which, provider, active, esc):
    # oracle: spec C2. Relation: adding an `escalation` model to any profile of either set
    # adds exactly that id to the (sorted, unique) list.
    from utils.config import configured_models

    base = _settings(provider=active, profiles=PROFILES, replacement_profiles=REPLACEMENT)
    sets = {"profiles": {p: dict(t) for p, t in PROFILES.items()},
            "replacement_profiles": {p: dict(t) for p, t in REPLACEMENT.items()}}
    sets[which][provider]["escalation"] = esc
    grown = _settings(provider=active, **sets)
    before = configured_models(base)
    after = configured_models(grown)
    assert len(before) == 13
    assert esc not in before
    assert after == sorted(before + [esc])


_ID = st.sampled_from([f"m{i}" for i in range(12)])
_PROFILE = st.fixed_dictionaries(
    {"plan": _ID, "answer": _ID, "complex": _ID},
    optional={"default": _ID, "escalation": _ID})


@pytest.mark.criterion("C2")
@settings(max_examples=60, deadline=None, suppress_health_check=_HC)
@given(profiles=st.fixed_dictionaries({"openai": _PROFILE, "anthropic": _PROFILE}),
       replacement=st.fixed_dictionaries({}, optional={"openai": _PROFILE,
                                                       "anthropic": _PROFILE}),
       provider=st.sampled_from(["openai", "anthropic"]),
       switch=st.booleans())
def test_property_union_of_all_tiers_and_resolved_fields(profiles, replacement, provider,
                                                         switch):
    # oracle: spec Requirement "Configured": every tier of every profile and replacement
    # profile plus the resolved model/plan/answer/complex(/escalation) fields; the result is
    # that set sorted, with no repeats.
    from utils.config import configured_models

    llm = _settings(provider=provider, profiles=profiles, replacement_profiles=replacement,
                    use_replacement_models=switch)
    expected = set()
    for group in (profiles, replacement):
        for tiers in group.values():
            expected.update(v for k, v in tiers.items() if k in TIERS and v is not None)
    expected.update([llm.model, llm.plan_model, llm.answer_model, llm.complex_model])
    if llm.escalation_model is not None:
        expected.add(llm.escalation_model)
    got = configured_models(llm)
    assert got == sorted(expected)
    assert len(got) == len(set(got)) > 0
