"""C2: load_settings_from rejects an effort outside its provider's set."""

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
# spec Notes: the effort sets.
ANTHROPIC = ("low", "medium", "high", "xhigh", "max")
OPENAI = ("none", "low", "medium", "high", "xhigh", "max")
SETS = {"openai": OPENAI, "anthropic": ANTHROPIC}


def _committed() -> dict:
    with _RAG.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _write(dirpath, cfg: dict) -> Path:
    path = Path(dirpath) / "rag.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def _with_effort(cfg: dict, group: str, provider: str, tier: str, effort: str) -> dict:
    out = copy.deepcopy(cfg)
    prof = out["llm"][group][provider]
    prof.setdefault("tier_options", {}).setdefault(tier, {})["effort"] = effort
    return out


@pytest.mark.criterion("C2")
def test_valid_efforts_load_and_bad_ones_raise(tmp_path):
    # oracle: spec Notes effort sets. A file with an in-set effort for every tier of both
    # groups and both providers loads (openai `none`, anthropic `xhigh`); `none` is not in
    # the Anthropic set, so the same file with it on an anthropic tier raises ValueError.
    from utils.config import RagSettings, load_settings_from

    cfg = _committed()
    for group in ("profiles", "replacement_profiles"):
        for tier in ("plan", "answer", "complex", "default", "escalation"):
            cfg = _with_effort(cfg, group, "openai", tier, "none")
            cfg = _with_effort(cfg, group, "anthropic", tier, "xhigh")
    got = load_settings_from(_write(tmp_path, cfg))
    assert isinstance(got, RagSettings)
    assert got.llm.profiles["anthropic"].tier_options["plan"].effort == "xhigh"
    bad = _with_effort(cfg, "profiles", "anthropic", "plan", "none")
    with pytest.raises(ValueError):
        load_settings_from(_write(tmp_path, bad))


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("group,provider,tier,effort", [
    ("profiles", "openai", "plan", "extreme"),
    ("profiles", "anthropic", "complex", "none"),
    ("replacement_profiles", "openai", "escalation", "minimal"),
    ("replacement_profiles", "anthropic", "default", "ultra"),
])
def test_out_of_set_effort_in_each_place_raises(tmp_path, group, provider, tier, effort):
    # oracle: spec C2: an effort outside the provider's set (Notes) in any profile or
    # replacement profile of either provider raises, whether or not that profile is active
    # (the committed file's active provider is openai, switch off).
    from utils.config import load_settings_from

    cfg = _committed()
    assert cfg["llm"]["provider"] == "openai"
    with pytest.raises(ValueError):
        load_settings_from(_write(tmp_path, _with_effort(cfg, group, provider, tier, effort)))


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@settings(max_examples=40, deadline=None, suppress_health_check=_HC)
@given(group=st.sampled_from(["profiles", "replacement_profiles"]),
       provider=st.sampled_from(["openai", "anthropic"]),
       tier=st.sampled_from(["plan", "answer", "complex", "default", "escalation"]),
       data=st.data())
def test_swapping_an_in_set_effort_for_an_out_of_set_one_flips_the_outcome(group, provider,
                                                                          tier, data):
    # oracle: spec Notes effort sets. Relation: one tier's effort drawn from its provider's
    # set loads; the same file with that effort replaced by a lowercase word outside the
    # set raises ValueError.
    from utils.config import RagSettings, load_settings_from

    good = data.draw(st.sampled_from(SETS[provider]), label="good")
    bad = data.draw(st.from_regex(r"[a-z]{1,9}", fullmatch=True)
                    .filter(lambda w: w not in SETS[provider]), label="bad")
    cfg = _committed()
    with tempfile.TemporaryDirectory() as d:
        ok = load_settings_from(_write(d, _with_effort(cfg, group, provider, tier, good)))
        assert isinstance(ok, RagSettings)
        assert getattr(ok.llm, group)[provider].tier_options[tier].effort == good
        with pytest.raises(ValueError):
            load_settings_from(_write(d, _with_effort(cfg, group, provider, tier, bad)))
