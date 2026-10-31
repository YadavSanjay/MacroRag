"""C8: validate_rate_card accepts well-formed cards and rejects every malformed shape."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_HC = [HealthCheck.function_scoped_fixture]
_CARD = Path(__file__).resolve().parents[3] / "configs" / "llm_pricing.yaml"

VALID = {"models": {
    "m-a": {"input": 1.0, "output": 2.0},
    "m-b": {"input": 0, "output": 5, "cache_read": 0.1, "cache_write": 1.25,
            "max_priced_prompt_tokens": 100000},
}}


def _committed() -> dict:
    with _CARD.open("rt", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _with_row(row: dict) -> dict:
    card = copy.deepcopy(VALID)
    card["models"]["m-x"] = row
    return card


BAD_CARDS = {
    "empty card": {},
    "models not a mapping (list)": {"models": [{"input": 1.0, "output": 2.0}]},
    "models not a mapping (None)": {"models": None},
    "models not a mapping (str)": {"models": "m-a"},
    "extra top-level key": {**VALID, "currency": "USD"},
    "row missing input": _with_row({"output": 2.0}),
    "row missing output": _with_row({"input": 1.0}),
    "mistyped cache_write": _with_row({"input": 1.0, "output": 2.0, "cache_wirte": 1.25}),
    "unknown row key": _with_row({"input": 1.0, "output": 2.0, "batch": 0.5}),
    "negative input": _with_row({"input": -1.0, "output": 2.0}),
    "negative output": _with_row({"input": 1.0, "output": -0.01}),
    "negative cache_read": _with_row({"input": 1.0, "output": 2.0, "cache_read": -0.1}),
    "negative cache_write": _with_row({"input": 1.0, "output": 2.0, "cache_write": -1}),
    "nan input": _with_row({"input": float("nan"), "output": 2.0}),
    "inf output": _with_row({"input": 1.0, "output": float("inf")}),
    "-inf cache_read": _with_row({"input": 1.0, "output": 2.0, "cache_read": float("-inf")}),
    "bool input": _with_row({"input": True, "output": 2.0}),
    "bool cache_write": _with_row({"input": 1.0, "output": 2.0, "cache_write": False}),
    "string input": _with_row({"input": "1.0", "output": 2.0}),
    "None output": _with_row({"input": 1.0, "output": None}),
    "list cache_read": _with_row({"input": 1.0, "output": 2.0, "cache_read": [0.1]}),
    "max tokens zero": _with_row({"input": 1.0, "output": 2.0, "max_priced_prompt_tokens": 0}),
    "max tokens negative": _with_row({"input": 1.0, "output": 2.0,
                                      "max_priced_prompt_tokens": -5}),
    "max tokens float": _with_row({"input": 1.0, "output": 2.0,
                                   "max_priced_prompt_tokens": 100000.0}),
    "max tokens bool": _with_row({"input": 1.0, "output": 2.0,
                                  "max_priced_prompt_tokens": True}),
    "max tokens string": _with_row({"input": 1.0, "output": 2.0,
                                    "max_priced_prompt_tokens": "100000"}),
}


@pytest.mark.criterion("C8")
def test_committed_and_hand_written_cards_pass_and_typo_fails():
    # oracle: spec C8 — the committed card (parsed from configs/llm_pricing.yaml) and a
    # hand-written card of valid rows return None; the Requirement's own example, a row with
    # `cache_wirte`, raises ValueError.
    from llm.pricing import validate_rate_card

    assert validate_rate_card(_committed()) is None
    assert validate_rate_card(copy.deepcopy(VALID)) is None
    with pytest.raises(ValueError):
        validate_rate_card(BAD_CARDS["mistyped cache_write"])


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("name", sorted(BAD_CARDS))
def test_each_malformed_card_raises(name):
    # oracle: spec C8 list of violations (one case per violation kind).
    from llm.pricing import validate_rate_card

    with pytest.raises(ValueError):
        validate_rate_card(copy.deepcopy(BAD_CARDS[name]))


_RATE = st.one_of(st.integers(0, 1000),
                  st.floats(min_value=0, max_value=1000, allow_nan=False,
                            allow_infinity=False))
_ROW = st.fixed_dictionaries(
    {"input": _RATE, "output": _RATE},
    optional={"cache_read": _RATE, "cache_write": _RATE,
              "max_priced_prompt_tokens": st.integers(1, 10_000_000)})
_CARDS = st.dictionaries(st.from_regex(r"[a-z][a-z0-9.\-]{0,15}", fullmatch=True), _ROW,
                         min_size=1, max_size=5)
_BAD_VALUES = {
    "input": [-1.0, float("nan"), float("inf"), True, "2", None],
    "output": [-0.5, float("-inf"), False, "x", None, [1.0]],
    "cache_read": [-1, float("nan"), True, "0.1"],
    "cache_write": [-2.5, float("inf"), False, None],
    "max_priced_prompt_tokens": [0, -1, 1.5, 100.0, True, "10"],
}


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@settings(max_examples=80, deadline=None, suppress_health_check=_HC)
@given(rows=_CARDS, data=st.data())
def test_property_valid_cards_pass_and_one_bad_field_fails(rows, data):
    # oracle: spec C8. Relation: any card built from valid rows returns None; corrupting one
    # row (bad value for a key, an unknown key, or a dropped required key) makes it raise.
    from llm.pricing import validate_rate_card

    card = {"models": copy.deepcopy(rows)}
    assert validate_rate_card(card) is None
    name = data.draw(st.sampled_from(sorted(rows)))
    kind = data.draw(st.sampled_from(["value", "extra key", "drop"]))
    bad = copy.deepcopy(card)
    if kind == "value":
        key = data.draw(st.sampled_from(sorted(_BAD_VALUES)))
        bad["models"][name][key] = data.draw(st.sampled_from(_BAD_VALUES[key]))
    elif kind == "extra key":
        bad["models"][name]["cache_wirte"] = 1.0
    else:
        bad["models"][name].pop(data.draw(st.sampled_from(["input", "output"])))
    with pytest.raises(ValueError):
        validate_rate_card(bad)
