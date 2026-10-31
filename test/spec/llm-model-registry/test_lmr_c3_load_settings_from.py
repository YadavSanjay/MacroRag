"""C3: load_settings_from loads YAML like load_settings and refuses unpriced configured models."""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HC = [HealthCheck.function_scoped_fixture]
_RAG = Path(__file__).resolve().parents[3] / "configs" / "rag.yaml"


def _committed() -> dict:
    with _RAG.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _write(dirpath, cfg: dict) -> Path:
    path = Path(dirpath) / "rag.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def _set(cfg: dict, place: tuple, value: str) -> dict:
    out = copy.deepcopy(cfg)
    node = out
    for key in place[:-1]:
        node = node.setdefault(key, {})
    node[place[-1]] = value
    return out


@pytest.mark.criterion("C3")
def test_committed_file_loads_like_load_settings(repo_root):
    # oracle: spec C3 — the committed configs/rag.yaml loads through load_settings_from to the
    # same model_dump() as load_settings(), and it names only priced models (Requirement).
    from utils.config import RagSettings, load_settings, load_settings_from

    got = load_settings_from(repo_root / "configs" / "rag.yaml")
    assert isinstance(got, RagSettings)
    assert got.model_dump() == load_settings().model_dump()
    assert got.llm.plan_model == "gpt-5.4-mini"


@pytest.mark.criterion("C3")
def test_all_priced_variant_returns_settings(tmp_path):
    # oracle: spec C3 — a file whose models are all priced returns settings; the flat
    # answer_model pin (a priced card row) wins over the active profile.
    from utils.config import RagSettings, load_settings_from

    cfg = _set(_committed(), ("llm", "answer_model"), "gpt-6-luna")
    got = load_settings_from(_write(tmp_path, cfg))
    assert isinstance(got, RagSettings)
    assert got.llm.answer_model == "gpt-6-luna"
    assert got.llm.complex_model == "gpt-5.4"


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("place", [
    ("llm", "answer_model"),
    ("llm", "profiles", "anthropic", "escalation"),
    ("llm", "replacement_profiles", "openai", "complex"),
    ("llm", "replacement_profiles", "anthropic", "plan"),
])
def test_unpriced_model_in_each_place_raises(tmp_path, place):
    # oracle: spec C3 — an unpriced id as a flat answer_model, as the escalation of the
    # inactive provider's profile (committed provider is openai), or as a replacement tier
    # makes loading raise ValueError naming it.
    from utils.config import load_settings_from

    cfg = _committed()
    assert cfg["llm"]["provider"] == "openai"
    path = _write(tmp_path, _set(cfg, place, "zz-unpriced-c3"))
    with pytest.raises(ValueError) as exc:
        load_settings_from(path)
    assert "zz-unpriced-c3" in str(exc.value)


@pytest.mark.criterion("C3")
def test_two_unpriced_models_are_both_named(tmp_path):
    # oracle: spec Requirement "raise ValueError naming every configured model that has no
    # rate-card row".
    from utils.config import load_settings_from

    cfg = _set(_committed(), ("llm", "answer_model"), "zz-unpriced-one")
    cfg = _set(cfg, ("llm", "replacement_profiles", "anthropic", "complex"), "zz-unpriced-two")
    with pytest.raises(ValueError) as exc:
        load_settings_from(_write(tmp_path, cfg))
    assert "zz-unpriced-one" in str(exc.value)
    assert "zz-unpriced-two" in str(exc.value)


_PLACES = [("llm", "answer_model"), ("llm", "plan_model"), ("llm", "complex_model"),
           ("llm", "escalation_model")]
_PLACES += [("llm", group, prov, tier)
            for group in ("profiles", "replacement_profiles")
            for prov in ("openai", "anthropic")
            for tier in ("plan", "answer", "complex", "default", "escalation")]
_PRICED = ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-6-sol", "gpt-6-luna",
           "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-8",
           "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5")


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@settings(max_examples=30, deadline=None, suppress_health_check=_HC)
@given(place=st.sampled_from(_PLACES), priced=st.sampled_from(_PRICED),
       suffix=st.from_regex(r"[a-z0-9]{1,8}", fullmatch=True),
       provider=st.sampled_from(["openai", "anthropic"]), switch=st.booleans())
def test_swapping_one_priced_id_for_an_unpriced_one_flips_the_outcome(place, priced, suffix,
                                                                      provider, switch):
    # oracle: spec Requirement (all providers checked, not only the active one; every
    # configured place). Relation: a committed config with one model place set to a priced
    # card row loads; the same file with that place set to an unpriced id raises naming it.
    from utils.config import RagSettings, load_settings_from

    base = _set(_committed(), ("llm", "provider"), provider)
    base = _set(base, ("llm", "use_replacement_models"), switch)
    unpriced = f"zz-unpriced-{suffix}"
    with tempfile.TemporaryDirectory() as d:
        ok = load_settings_from(_write(d, _set(base, place, priced)))
        assert isinstance(ok, RagSettings)
        assert ok.llm.provider == provider
        with pytest.raises(ValueError) as exc:
            load_settings_from(_write(d, _set(base, place, unpriced)))
    assert unpriced in str(exc.value)
