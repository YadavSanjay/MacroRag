"""C1: tier_options_for returns the active profile's TierOptions for a tier."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

PLAN = {"max_tokens": 1500, "effort": "low", "timeout_s": 20.0}
COMPLEX = {"max_tokens": 9000, "effort": "high"}
R_PLAN = {"max_tokens": 2500, "effort": "medium", "timeout_s": 45.0}
A_ANSWER = {"max_tokens": 700, "effort": "low"}


def _opts(**kw):
    from utils.config import TierOptions

    return TierOptions(**kw)


@pytest.mark.criterion("C1")
def test_active_profile_options_and_empty_cases(lob):
    # oracle: spec C1 / Requirement "Per-tier options": with provider openai and the switch
    # off, the openai `profiles` entry supplies each tier's options; None, a tier without an
    # entry and the inactive replacement profile give TierOptions() (all None).
    from utils.config import TierOptions, tier_options_for

    llm = lob.llm_settings("openai",
                           options={"openai": {"plan": PLAN, "complex": COMPLEX},
                                    "anthropic": {"answer": A_ANSWER}},
                           replacement_options={"openai": {"plan": R_PLAN}})
    assert tier_options_for(llm, "plan") == _opts(**PLAN)
    assert tier_options_for(llm, "complex") == _opts(**COMPLEX)
    assert tier_options_for(llm, "answer") == TierOptions()
    assert tier_options_for(llm, "escalation") == TierOptions()
    assert tier_options_for(llm, None) == TierOptions()


@pytest.mark.criterion("C1")
def test_follows_replacement_switch_and_provider(lob):
    # oracle: spec Requirement: the active provider's `replacement_profiles` entry when
    # use_replacement_models is on and that entry exists, else its `profiles` entry; it
    # follows llm.provider.
    from utils.config import TierOptions, tier_options_for

    opts = {"openai": {"plan": PLAN}, "anthropic": {"answer": A_ANSWER}}
    ropts = {"openai": {"plan": R_PLAN}}
    on = lob.llm_settings("openai", options=opts, replacement_options=ropts,
                          use_replacement=True)
    assert tier_options_for(on, "plan") == _opts(**R_PLAN)
    anth = lob.llm_settings("anthropic", options=opts, replacement_options=ropts)
    assert tier_options_for(anth, "answer") == _opts(**A_ANSWER)
    assert tier_options_for(anth, "plan") == TierOptions()


@pytest.mark.criterion("C1")
def test_missing_replacement_entry_falls_back_to_profiles(lob):
    # oracle: spec Requirement: the switch is on but the active provider has no
    # replacement entry, so its `profiles` entry applies; a provider with no profile at
    # all gives TierOptions().
    from utils.config import LLMSettings, TierOptions, tier_options_for

    llm = lob.llm_settings("anthropic", options={"anthropic": {"answer": A_ANSWER}},
                           use_replacement=True)
    llm.replacement_profiles.pop("anthropic")
    assert tier_options_for(llm, "answer") == _opts(**A_ANSWER)
    bare = LLMSettings.model_validate({
        "provider": "anthropic",
        "profiles": {"openai": {"plan": "gpt-5.4-mini", "answer": "gpt-5.4-nano",
                                "complex": "gpt-5.4",
                                "tier_options": {"plan": PLAN}}},
        "replacement_profiles": {}})
    assert tier_options_for(bare, "plan") == TierOptions()
    assert tier_options_for(lob.llm_settings("openai", options={"openai": {"plan": PLAN}}),
                            "plan") == _opts(**PLAN)


_OPT = st.fixed_dictionaries({}, optional={
    "max_tokens": st.integers(1, 64_000),
    "effort": st.sampled_from(["none", "low", "medium", "high", "xhigh", "max"]),
    "timeout_s": st.integers(1, 600).map(float)})
_GROUP = st.dictionaries(st.sampled_from(["openai", "anthropic"]),
                         st.dictionaries(st.sampled_from(
                             ["plan", "answer", "complex", "default", "escalation"]),
                             _OPT, max_size=5), max_size=2)


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None)
@given(provider=st.sampled_from(["openai", "anthropic"]), switch=st.booleans(),
       opts=_GROUP, ropts=_GROUP,
       tier=st.sampled_from(["plan", "answer", "complex", "default", "escalation", None]))
def test_property_matches_the_active_profile(lob, provider, switch, opts, ropts, tier):
    # oracle: spec Requirement "Per-tier options", computed in the test: the expected options
    # are looked up in the dict the settings were built from (replacement group when the
    # switch is on, else profiles; both code-default groups have both providers).
    from utils.config import TierOptions, tier_options_for

    llm = lob.llm_settings(provider, options=opts, replacement_options=ropts,
                           use_replacement=switch)
    group = ropts if switch else opts
    want = (TierOptions(**group.get(provider, {}).get(tier, {}))
            if tier is not None else TierOptions())
    # A sentinel tier with known non-default options pins the lookup itself.
    probe = lob.llm_settings(provider, options={provider: {"default": PLAN}},
                             replacement_options={provider: {"default": R_PLAN}},
                             use_replacement=switch)
    assert tier_options_for(probe, "default") == _opts(**(R_PLAN if switch else PLAN))
    assert tier_options_for(llm, tier) == want
