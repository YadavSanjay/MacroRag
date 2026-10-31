"""C8: the answer-cache version follows the models, max_tokens and active tier options."""

from __future__ import annotations

import copy
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _base():
    from utils.config import RagSettings

    return RagSettings()


def _opt(**kw):
    from utils.config import TierOptions

    return TierOptions(**kw)


def _changed(fn):
    s = copy.deepcopy(_base())
    fn(s)
    return s


# Each mutator changes one cache_version input (spec Requirement): openai is the active
# provider of RagSettings() and the replacement switch is off.
def _plan_model(s): s.llm.plan_model = "gpt-5.4-nano"
def _answer_model(s): s.llm.answer_model = "gpt-5.4-mini"
def _complex_model(s): s.llm.complex_model = "gpt-6-sol"
def _max_tokens(s): s.llm.max_tokens = 6000
def _plan_opts(s): s.llm.profiles["openai"].tier_options["plan"] = _opt(max_tokens=1500)
def _answer_opts(s): s.llm.profiles["openai"].tier_options["answer"] = _opt(effort="low")
def _complex_opts(s): s.llm.profiles["openai"].tier_options["complex"] = _opt(timeout_s=90.0)


# Each mutator changes something cache_version does not hash.
def _trace(s): s.trace.enabled = not s.trace.enabled
def _other_provider(s): s.llm.profiles["anthropic"].tier_options["plan"] = _opt(max_tokens=99)
def _inactive_replacement(s):
    s.llm.replacement_profiles["openai"].tier_options["answer"] = _opt(effort="max")


INPUTS = [_plan_model, _answer_model, _complex_model, _max_tokens, _plan_opts, _answer_opts,
          _complex_opts]
UNRELATED = [_trace, _other_provider, _inactive_replacement]


@pytest.mark.criterion("C8")
def test_equal_settings_agree_and_each_input_changes_it():
    # oracle: spec Requirement / C8: equal for equal settings; changes when plan_model,
    # answer_model, complex_model, llm.max_tokens or the active profile's plan / answer /
    # complex options change.
    from pipelines.query_pipeline import cache_version

    base = cache_version(_base())
    assert isinstance(base, str) and base
    assert cache_version(_base()) == base
    for fn in INPUTS:
        assert cache_version(_changed(fn)) != base, fn.__name__


@pytest.mark.criterion("C8")
def test_unrelated_settings_do_not_change_it():
    # oracle: spec C8: trace.enabled and another provider's profile options are not inputs;
    # neither is the replacement profile while the switch is off (Requirement: the active
    # profile's options). Paired with a real input so a constant cannot pass.
    from pipelines.query_pipeline import cache_version

    base = cache_version(_base())
    for fn in UNRELATED:
        assert cache_version(_changed(fn)) == base, fn.__name__
    assert cache_version(_changed(_plan_opts)) != base


@pytest.mark.criterion("C8")
def test_active_replacement_profile_options_are_hashed():
    # oracle: spec Requirement: with use_replacement_models on, the replacement entry is the
    # active profile, so its plan options change the version (and the profiles entry's no
    # longer do).
    from pipelines.query_pipeline import cache_version
    from utils.config import RagSettings

    on = RagSettings.model_validate({"llm": {"use_replacement_models": True}})
    base = cache_version(on)
    rep = copy.deepcopy(on)
    rep.llm.replacement_profiles["openai"].tier_options["plan"] = _opt(effort="high")
    assert cache_version(rep) != base
    old = copy.deepcopy(on)
    old.llm.profiles["openai"].tier_options["plan"] = _opt(effort="high")
    assert cache_version(old) == base


@pytest.mark.criterion("C8")
@settings(max_examples=30, deadline=None)
@given(changes=st.lists(st.sampled_from(INPUTS), min_size=1, max_size=4, unique=True),
       noise=st.lists(st.sampled_from(UNRELATED), max_size=3, unique=True))
def test_property_inputs_change_and_noise_does_not(changes, noise):
    # oracle: spec Requirement. Any non-empty set of input changes moves the version; adding
    # unrelated changes on top moves nothing further.
    from pipelines.query_pipeline import cache_version

    base = cache_version(_base())
    s = copy.deepcopy(_base())
    for fn in changes:
        fn(s)
    moved = cache_version(s)
    assert moved != base
    for fn in noise:
        fn(s)
    assert cache_version(s) == moved
    quiet = copy.deepcopy(_base())
    for fn in noise:
        fn(quiet)
    assert cache_version(quiet) == base


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
def test_pipeline_cache_misses_after_a_tier_option_change(lob):
    # oracle: spec C8 and .claude/rules/rag.md (cache keys change with model tier).
    # Relation: two pipelines sharing one L1 cache and one corpus digest: with equal settings
    # the second answer is a cache hit; when only the active profile's answer-tier options
    # differ, it is a miss.
    from utils.cache import InMemoryCache

    def run(mutate):
        l1 = InMemoryCache()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = lob.pipeline_settings(verify=False)
            lob.build(a, lob.TierRecorder(), first, l1=l1).answer(lob.QUERY)
            second = lob.pipeline_settings(verify=False)
            mutate(second)
            return lob.build(b, lob.TierRecorder(), second, l1=l1).answer(lob.QUERY)

    assert run(lambda s: None).cached is True
    changed = run(lambda s: s.llm.profiles["openai"].tier_options.__setitem__(
        "answer", _opt(max_tokens=1200)))
    assert changed.cached is False
