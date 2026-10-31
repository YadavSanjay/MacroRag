"""C1-C8: a config switch to replacement OpenAI and Claude models, off by default."""

from __future__ import annotations

import os
import string
from contextlib import contextmanager

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

ENV_VAR = "MACRORAG_USE_REPLACEMENT_MODELS"
TIERS = ("plan", "answer", "complex", "default")

# Copied from the spec (C1: `_DEFAULT_PROFILES` on main).
CURRENT_OPENAI = {"plan": "gpt-5.4-mini", "answer": "gpt-5.4-nano",
                  "complex": "gpt-5.4", "default": "gpt-5.4-nano"}
CURRENT_ANTHROPIC = {"plan": "claude-sonnet-4-6", "answer": "claude-haiku-4-5",
                     "complex": "claude-opus-4-8", "default": "claude-haiku-4-5"}
# Copied from the spec (C2: OpenAI deprecations page + the operator's choice in Notes;
# Anthropic models overview).
REPLACEMENT_OPENAI = {"plan": "gpt-6-sol", "answer": "gpt-6-luna",
                      "complex": "gpt-6-sol", "default": "gpt-6-luna"}
REPLACEMENT_ANTHROPIC = {"plan": "claude-sonnet-5-5", "answer": "claude-haiku-5-5",
                         "complex": "claude-opus-5-5", "default": "claude-haiku-5-5"}
CURRENT = {"openai": CURRENT_OPENAI, "anthropic": CURRENT_ANTHROPIC}
REPLACEMENT = {"openai": REPLACEMENT_OPENAI, "anthropic": REPLACEMENT_ANTHROPIC}

TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off")

# Copied from the spec (C7): USD per 1M tokens, (input, cache read, output).
RATES = {
    "claude-opus-5-5": (4.00, 0.20, 20.00),
    "claude-sonnet-5-5": (2.00, 0.20, 10.00),
    "claude-haiku-5-5": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "claude-opus-4-8": (5.00, 0.50, 25.00),
    "claude-sonnet-4-6": (3.00, 0.30, 15.00),
    "claude-haiku-4-5": (1.00, 0.10, 5.00),
}

# Copied from the spec (C6).
NO_SAMPLING_PREFIXES = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-5", "claude-fable-5",
                        "claude-mythos-5", "claude-opus-4-8", "claude-opus-4-7")
SAMPLING_IDS = ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6")

# The autouse conftest fixtures are function-scoped; each example below sets and restores
# the environment itself, so sharing them across examples is safe.
_HC = [HealthCheck.function_scoped_fixture]

# ASCII only, so "ignoring case and surrounding whitespace" has one unambiguous meaning.
_ASCII = string.ascii_letters + string.digits + string.punctuation + " "
_PADDING = st.sampled_from(["", " ", "  ", "\t", "\n", " \t "])


@contextmanager
def _env(value):
    """Set (or, for None, unset) the switch variable for the block, then restore it."""
    old = os.environ.get(ENV_VAR)
    if value is None:
        os.environ.pop(ENV_VAR, None)
    else:
        os.environ[ENV_VAR] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = old


def _llm_tiers(llm) -> dict:
    return {"plan": llm.plan_model, "answer": llm.answer_model,
            "complex": llm.complex_model, "default": llm.model}


def _profile_tiers(profile) -> dict:
    return {t: getattr(profile, t) for t in TIERS}


@st.composite
def _spelling(draw, words):
    """One of `words` in random letter case with random surrounding whitespace."""
    word = draw(st.sampled_from(words))
    flips = draw(st.lists(st.booleans(), min_size=len(word), max_size=len(word)))
    cased = "".join(c.upper() if f else c.lower() for c, f in zip(word, flips, strict=True))
    return draw(_PADDING) + cased + draw(_PADDING)


# ----------------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_current_set_profiles():
    # oracle: spec C1, values copied into CURRENT.
    from utils.config import default_profiles

    assert default_profiles(False) == CURRENT


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_current_set_unaffected_by_replacement_calls():
    # oracle: spec C1 + C2. Relation: the current set is the same before and after the
    # replacement set is requested and mutated, and equals the C1 table both times.
    from utils.config import default_profiles

    before = default_profiles(False)
    other = default_profiles(True)
    other["openai"]["plan"] = "mutated"
    other["anthropic"].clear()
    after = default_profiles(False)
    assert before == after == CURRENT


# ----------------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_replacement_set_profiles_and_fresh_copy():
    # oracle: spec C2, values copied into REPLACEMENT; the mutation of
    # the first result must not show in the second.
    from utils.config import default_profiles

    first = default_profiles(True)
    assert first == REPLACEMENT
    first["openai"]["answer"] = "mutated"
    first["anthropic"]["plan"] = "mutated"
    first["extra"] = {}
    assert default_profiles(True) == REPLACEMENT


@pytest.mark.criterion("C2")
@settings(max_examples=25, deadline=None, suppress_health_check=_HC)
@given(flags=st.lists(st.booleans(), min_size=1, max_size=6))
def test_property_any_call_sequence_returns_fresh_tables(flags):
    # oracle: spec C1/C2 tables, chosen by flag in the test; every result is mutated after
    # it is checked, and no later result may see it.
    from utils.config import default_profiles

    for flag in flags:
        got = default_profiles(flag)
        assert got == (REPLACEMENT if flag else CURRENT)
        for tiers in got.values():
            for tier in list(tiers):
                tiers[tier] = "mutated"
        got.pop("openai")


# ----------------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_absent_key_returns_default():
    # oracle: spec C3 — no key: the result is `default`, for either default.
    from utils.config import replacement_models_enabled

    env = {"UNRELATED": "1"}
    assert replacement_models_enabled(env, True) is True
    assert replacement_models_enabled(env, False) is False
    assert replacement_models_enabled({}) is False


@pytest.mark.criterion("C3")
def test_recognised_values_override_default():
    # oracle: spec C3 — the listed words, whatever default is.
    from utils.config import replacement_models_enabled

    for default in (True, False):
        for word in TRUE_WORDS:
            assert replacement_models_enabled({ENV_VAR: word}, default) is True, (word, default)
        for word in FALSE_WORDS:
            assert replacement_models_enabled({ENV_VAR: word}, default) is False, (word, default)
    assert replacement_models_enabled({ENV_VAR: "  TRUE "}, False) is True
    assert replacement_models_enabled({ENV_VAR: "\tOff\n"}, True) is False


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("value", ["", " ", "2", "maybe", "enabled", "y", "tru"])
def test_unknown_values_raise(value):
    # oracle: spec C3 — any other value (including empty) raises ValueError.
    from utils.config import replacement_models_enabled

    for default in (True, False):
        with pytest.raises(ValueError):
            replacement_models_enabled({ENV_VAR: value}, default)


@pytest.mark.criterion("C3")
def test_env_none_reads_os_environ(monkeypatch):
    # oracle: spec C3 — env=None reads os.environ.
    from utils.config import replacement_models_enabled

    assert replacement_models_enabled(None, True) is True
    monkeypatch.setenv(ENV_VAR, "on")
    assert replacement_models_enabled(None, False) is True
    assert replacement_models_enabled(default=False) is True
    monkeypatch.setenv(ENV_VAR, "off")
    assert replacement_models_enabled(None, True) is False


@pytest.mark.criterion("C3")
@settings(max_examples=200, deadline=None, suppress_health_check=_HC)
@given(on=_spelling(TRUE_WORDS), off=_spelling(FALSE_WORDS), default=st.booleans())
def test_property_case_and_whitespace_are_ignored(on, off, default):
    # oracle: spec C3 — the words in any case with any surrounding whitespace keep their
    # meaning, whatever default is.
    from utils.config import replacement_models_enabled

    assert replacement_models_enabled({ENV_VAR: on}, default) is True
    assert replacement_models_enabled({ENV_VAR: off}, default) is False


@pytest.mark.criterion("C3")
@settings(max_examples=200, deadline=None, suppress_health_check=_HC)
@given(value=st.text(alphabet=_ASCII, max_size=8).filter(
    lambda s: s.strip().lower() not in TRUE_WORDS + FALSE_WORDS), default=st.booleans())
def test_property_every_other_value_raises(value, default):
    # oracle: spec C3 — a value that is not a listed word after trimming and lowering raises.
    from utils.config import replacement_models_enabled

    with pytest.raises(ValueError):
        replacement_models_enabled({ENV_VAR: value}, default)


# ----------------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_llm_settings_field_selects_the_set():
    # oracle: spec C4 (values from C1/C2), variable unset.
    from utils.config import LLMSettings

    assert _llm_tiers(LLMSettings(provider="openai")) == CURRENT_OPENAI
    assert _llm_tiers(LLMSettings(provider="openai", use_replacement_models=True)) \
        == REPLACEMENT_OPENAI


@pytest.mark.criterion("C4")
def test_variable_overrides_field_both_ways(monkeypatch):
    # oracle: spec C4 — the variable overrides the field in both directions.
    from utils.config import LLMSettings

    monkeypatch.setenv(ENV_VAR, "1")
    assert _llm_tiers(LLMSettings(provider="openai")) == REPLACEMENT_OPENAI
    assert _llm_tiers(LLMSettings(provider="openai", use_replacement_models=False)) \
        == REPLACEMENT_OPENAI
    monkeypatch.setenv(ENV_VAR, "0")
    assert _llm_tiers(LLMSettings(provider="openai", use_replacement_models=True)) \
        == CURRENT_OPENAI


@pytest.mark.criterion("C4")
def test_explicit_flat_tier_wins_over_replacement_set():
    # oracle: spec C4 — an explicit plan_model wins; the other tiers come from the
    # replacement set (C2).
    from utils.config import LLMSettings

    llm = LLMSettings(provider="openai", use_replacement_models=True, plan_model="gpt-5.4-mini")
    assert _llm_tiers(llm) == {**REPLACEMENT_OPENAI, "plan": "gpt-5.4-mini"}


@pytest.mark.criterion("C4")
def test_anthropic_tiers_follow_the_switch(monkeypatch):
    # oracle: spec C4 — for provider anthropic the field selects C1's or C2's Anthropic
    # tiers, and the variable overrides the field in both directions.
    from utils.config import LLMSettings

    assert _llm_tiers(LLMSettings(provider="anthropic")) == CURRENT_ANTHROPIC
    assert _llm_tiers(LLMSettings(provider="anthropic", use_replacement_models=True)) \
        == REPLACEMENT_ANTHROPIC
    monkeypatch.setenv(ENV_VAR, "yes")
    assert _llm_tiers(LLMSettings(provider="anthropic")) == REPLACEMENT_ANTHROPIC
    monkeypatch.setenv(ENV_VAR, "off")
    assert _llm_tiers(LLMSettings(provider="anthropic", use_replacement_models=True)) \
        == CURRENT_ANTHROPIC


@pytest.mark.criterion("C4")
@settings(max_examples=60, deadline=None, suppress_health_check=_HC)
@given(field=st.booleans(),
       env=st.one_of(st.none(), _spelling(TRUE_WORDS), _spelling(FALSE_WORDS)),
       provider=st.sampled_from(["openai", "anthropic"]))
def test_property_selected_set_follows_variable_then_field(field, env, provider):
    # oracle: spec C4, expected set computed in the test: the variable's meaning (C3) when
    # set, else the field; the provider's C2 tiers when on, its C1 tiers when off.
    from utils.config import LLMSettings

    on = field if env is None else env.strip().lower() in TRUE_WORDS
    with _env(env):
        llm = LLMSettings(provider=provider, use_replacement_models=field)
    assert _llm_tiers(llm) == (REPLACEMENT if on else CURRENT)[provider]


# ----------------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_committed_config_switch_off():
    # oracle: spec C5 — committed rag.yaml, variable unset: the switch is off (requirement:
    # `llm.use_replacement_models`, off by default) and no model moves (C1 tiers).
    from utils.config import load_settings

    llm = load_settings().llm
    assert getattr(llm, "use_replacement_models", None) is False
    assert llm.provider == "openai"
    assert _llm_tiers(llm) == CURRENT_OPENAI
    assert _profile_tiers(llm.profiles["anthropic"]) == CURRENT_ANTHROPIC


@pytest.mark.criterion("C5")
def test_committed_config_variable_on(monkeypatch):
    # oracle: spec C5 — committed rag.yaml with the variable "1" gives the C2 OpenAI tiers.
    from utils.config import load_settings

    monkeypatch.setenv(ENV_VAR, "1")
    llm = load_settings().llm
    assert llm.provider == "openai"
    assert _llm_tiers(llm) == REPLACEMENT_OPENAI


@pytest.mark.criterion("C5")
@settings(max_examples=12, deadline=None, suppress_health_check=_HC)
@given(env=st.one_of(st.none(), _spelling(TRUE_WORDS), _spelling(FALSE_WORDS)))
def test_property_committed_config_follows_variable(env):
    # oracle: spec C5, expected OpenAI tiers computed in the test from the variable's
    # meaning (C3): C2's when on, C1's when unset or off.
    from utils.config import load_settings

    on = env is not None and env.strip().lower() in TRUE_WORDS
    load_settings.cache_clear()
    try:
        with _env(env):
            llm = load_settings().llm
    finally:
        load_settings.cache_clear()
    assert llm.provider == "openai"
    assert _llm_tiers(llm) == (REPLACEMENT_OPENAI if on else CURRENT_OPENAI)


# ----------------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
def test_temperature_by_model():
    # oracle: spec C6 (Anthropic API reference, sampling parameters per model).
    from llm.anthropic_client import _accepts_temperature

    for model in ("claude-opus-5-5", "claude-haiku-5-5", "claude-sonnet-5-5",
                  "claude-fable-5", "claude-mythos-5", "claude-opus-4-8", "claude-opus-4-7"):
        assert _accepts_temperature(model) is False, model
    for model in SAMPLING_IDS:
        assert _accepts_temperature(model) is True, model


@pytest.mark.criterion("C6")
@settings(max_examples=100, deadline=None, suppress_health_check=_HC)
@given(suffix=st.text(alphabet=string.ascii_lowercase + string.digits + "-.", max_size=12))
def test_property_no_temperature_whatever_follows(suffix):
    # oracle: spec C6 — every id starting with a listed prefix is called without
    # temperature, whatever follows; the 4.6-and-older ids keep it.
    from llm.anthropic_client import _accepts_temperature

    assert [p for p in NO_SAMPLING_PREFIXES if _accepts_temperature(p + suffix)] == []
    assert all(_accepts_temperature(m) is True for m in SAMPLING_IDS)


# ----------------------------------------------------------------------------------- C7
@pytest.mark.criterion("C7")
def test_rates_per_model():
    # oracle: spec C7 rate table (Anthropic + OpenAI pricing, read 2026-10-17); 1M tokens
    # of one kind costs exactly that kind's rate. abs=1e-9 USD is the spec's tolerance
    # (float rounding of a rate divided by 1e6 and multiplied back).
    from llm.pricing import cost_usd

    for model, (rate_in, rate_cache, rate_out) in RATES.items():
        assert cost_usd(model, prompt_tokens=1_000_000) == pytest.approx(rate_in, abs=1e-9), model
        assert cost_usd(model, completion_tokens=1_000_000) \
            == pytest.approx(rate_out, abs=1e-9), model
        assert cost_usd(model, prompt_tokens=1_000_000, cached_tokens=1_000_000) \
            == pytest.approx(rate_cache, abs=1e-9), model


@pytest.mark.criterion("C7")
def test_every_model_in_either_set_is_priced():
    # oracle: spec C7 + C1/C2 — the union of both sets is exactly the eleven models of the
    # rate table, and each has a cost.
    from llm.pricing import cost_usd
    from utils.config import default_profiles

    models = {m for flag in (False, True)
              for tiers in default_profiles(flag).values() for m in tiers.values()}
    assert models == set(RATES)
    assert [m for m in sorted(models) if cost_usd(m, prompt_tokens=1) is None] == []


@pytest.mark.criterion("C7")
@settings(max_examples=100, deadline=None, suppress_health_check=_HC)
@given(cached=st.integers(0, 200_000), uncached=st.integers(0, 200_000),
       out=st.integers(0, 50_000))
def test_property_cost_is_linear_in_the_rates(cached, uncached, out):
    # oracle: spec C7 rates; cost computed in the test as
    # (uncached*input + cached*cache_read + out*output) / 1e6, for every model. abs=1e-9 USD
    # is the spec's tolerance (float rounding).
    from llm.pricing import cost_usd

    for model, (rate_in, rate_cache, rate_out) in RATES.items():
        expected = (uncached * rate_in + cached * rate_cache + out * rate_out) / 1_000_000
        got = cost_usd(model, prompt_tokens=uncached + cached, completion_tokens=out,
                       cached_tokens=cached)
        assert got == pytest.approx(expected, abs=1e-9), model


# ----------------------------------------------------------------------------------- C8
OPENAI_SAMPLING_IDS = ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano")


@pytest.mark.criterion("C8")
def test_openai_temperature_by_model():
    # oracle: spec C8 — OpenAI API error for temperature=0.1 on gpt-6-sol and gpt-6-luna
    # ("Only the default (1) value is supported", 2026-10-17); the gpt-5.4 family runs with
    # 0.1 in production today.
    from llm.openai_client import accepts_temperature

    for model in ("gpt-6-sol", "gpt-6-luna", "gpt-6"):
        assert accepts_temperature(model) is False, model
    for model in OPENAI_SAMPLING_IDS:
        assert accepts_temperature(model) is True, model


@pytest.mark.criterion("C8")
@settings(max_examples=100, deadline=None, suppress_health_check=_HC)
@given(suffix=st.text(alphabet=string.ascii_lowercase + string.digits + "-.", max_size=12))
def test_property_openai_no_temperature_whatever_follows_gpt6(suffix):
    # oracle: spec C8 — every id starting with gpt-6 is False whatever follows (OpenAI API
    # error, 2026-10-17); gpt-5.4, gpt-5.4-mini and gpt-5.4-nano stay True.
    from llm.openai_client import accepts_temperature

    assert accepts_temperature("gpt-6" + suffix) is False
    assert all(accepts_temperature(m) is True for m in OPENAI_SAMPLING_IDS)
