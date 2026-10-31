"""C4: matrix_models resolves the eval-matrix model list and judge model, all priced."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HC = [HealthCheck.function_scoped_fixture]
PRICED = ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-6-sol", "gpt-6-luna",
          "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-8",
          "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5")
UNPRICED = ("zz-unpriced-m1", "zz-unpriced-m2", "zz-unpriced-m3")
_PAD = st.sampled_from(["", " ", "  ", "\t"])


def _llm(**kw):
    from utils.config import LLMSettings

    base = {"plan_model": "claude-sonnet-4-6", "answer_model": "gpt-5.4-nano",
            "complex_model": "gpt-6-sol"}
    base.update(kw)
    return LLMSettings(**base)


@pytest.mark.criterion("C4")
def test_explicit_models_split_and_stripped():
    # oracle: spec C4 + the current eval-matrix argument handling in pipelines/cli.py:
    # comma-split, whitespace-stripped, empty entries dropped, order kept; judge given.
    from pipelines.cli import matrix_models

    got = matrix_models(" gpt-5.4 , ,claude-haiku-5-5,,gpt-6-luna ", "gpt-5.4-mini", _llm())
    assert got == (["gpt-5.4", "claude-haiku-5-5", "gpt-6-luna"], "gpt-5.4-mini")


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("models_arg", [None, ""])
@pytest.mark.parametrize("judge_arg", [None, ""])
def test_defaults_from_settings(models_arg, judge_arg):
    # oracle: spec C4 — no --models: [answer_model, complex_model]; no --judge-model:
    # plan_model (hand-set in _llm: gpt-5.4-nano, gpt-6-sol, claude-sonnet-4-6).
    from pipelines.cli import matrix_models

    got = matrix_models(models_arg, judge_arg, _llm())
    assert got == (["gpt-5.4-nano", "gpt-6-sol"], "claude-sonnet-4-6")


@pytest.mark.criterion("C4")
def test_unpriced_models_and_judge_are_all_named():
    # oracle: spec C4 — ValueError naming every unpriced id among models and judge_model.
    from pipelines.cli import matrix_models

    with pytest.raises(ValueError) as exc:
        matrix_models("gpt-5.4,zz-unpriced-m1, zz-unpriced-m2", "zz-unpriced-m3", _llm())
    msg = str(exc.value)
    for i in ("zz-unpriced-m1", "zz-unpriced-m2", "zz-unpriced-m3"):
        assert i in msg


@pytest.mark.criterion("C4")
def test_unpriced_defaults_from_settings_raise():
    # oracle: spec C4 — the defaults are checked too: an unpriced answer_model (models
    # default) and an unpriced plan_model (judge default) are both named.
    from pipelines.cli import matrix_models

    llm = _llm(answer_model="zz-unpriced-m1", plan_model="zz-unpriced-m2")
    with pytest.raises(ValueError) as exc:
        matrix_models(None, None, llm)
    assert "zz-unpriced-m1" in str(exc.value)
    assert "zz-unpriced-m2" in str(exc.value)
    # A priced explicit list and judge override the unpriced defaults and pass.
    assert matrix_models("gpt-5.4", "gpt-5.4", llm) == (["gpt-5.4"], "gpt-5.4")


@pytest.mark.criterion("C4")
@settings(max_examples=60, deadline=None, suppress_health_check=_HC)
@given(ids=st.lists(st.sampled_from(PRICED + UNPRICED), min_size=1, max_size=6),
       judge=st.one_of(st.none(), st.just(""), st.sampled_from(PRICED + UNPRICED)),
       data=st.data())
def test_property_split_order_and_unpriced_refusal(ids, judge, data):
    # oracle: spec C4 — models are the non-empty stripped entries in their given order;
    # judge is the argument or plan_model; ValueError iff any of them is unpriced, naming
    # every unpriced one.
    from pipelines.cli import matrix_models

    parts = []
    for i in ids:
        parts.extend([""] * data.draw(st.integers(0, 1)))
        parts.append(data.draw(_PAD) + i + data.draw(_PAD))
    arg = ",".join(parts)
    llm = _llm()
    want_judge = judge if judge else "claude-sonnet-4-6"
    bad = sorted({m for m in ids + [want_judge] if m in UNPRICED})
    if bad:
        with pytest.raises(ValueError) as exc:
            matrix_models(arg, judge, llm)
        for m in bad:
            assert m in str(exc.value)
    else:
        assert matrix_models(arg, judge, llm) == (list(ids), want_judge)
    # A must-pass case in every example: the same list with unpriced ids replaced.
    clean = [m if m not in UNPRICED else "gpt-5.4" for m in ids]
    assert matrix_models(",".join(clean), None, llm) == (clean, "claude-sonnet-4-6")
