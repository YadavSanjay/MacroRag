"""C1: require_priced passes when every id has a rate-card row, else names every unpriced id."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# The committed card's row names (configs/llm_pricing.yaml, spec Notes table + OpenAI rows).
CARD_ROWS = ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-6-sol", "gpt-6-luna",
             "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-8",
             "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5")
# Priced through the longest row-name prefix (as llm.pricing.model_price resolves them).
PREFIX_PRICED = ("gpt-5.4-mini-2026-01", "claude-sonnet-4-6-20260101", "gpt-6-luna-preview")
PRICED = CARD_ROWS + PREFIX_PRICED
# No row name is a prefix of these.
UNPRICED = ("zz-unpriced-alpha", "zz-unpriced-beta", "zz-unpriced-gamma", "zz-unpriced-delta")

_HC = [HealthCheck.function_scoped_fixture]


def _outcome(models):
    """('ok', None) when require_priced returns None; ('raise', message) on ValueError."""
    from llm.pricing import require_priced

    try:
        out = require_priced(models)
    except ValueError as exc:
        return ("raise", str(exc))
    assert out is None, out
    return ("ok", None)


@pytest.mark.criterion("C1")
def test_all_priced_passes_and_unpriced_ids_are_all_named():
    # oracle: spec C1 + the committed card's row names (copied into CARD_ROWS); an id priced
    # by exact row or longest row-name prefix passes, any unpriced id is named in the error.
    from llm.pricing import require_priced

    assert require_priced(list(PRICED)) is None
    with pytest.raises(ValueError) as exc:
        require_priced(["gpt-5.4", "zz-unpriced-alpha", "claude-haiku-4-5", "zz-unpriced-beta"])
    msg = str(exc.value)
    assert "zz-unpriced-alpha" in msg
    assert "zz-unpriced-beta" in msg


@pytest.mark.criterion("C1")
def test_single_unpriced_id_raises_and_empty_list_passes():
    # oracle: spec C1 — one unpriced id among priced ones is enough to raise; an empty list
    # names no unpriced id, so it passes (the "every id has a row" condition holds vacuously).
    from llm.pricing import require_priced

    with pytest.raises(ValueError) as exc:
        require_priced(("claude-opus-5-5", "zz-unpriced-gamma"))
    assert "zz-unpriced-gamma" in str(exc.value)
    assert require_priced([]) is None


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None, suppress_health_check=_HC)
@given(ids=st.lists(st.sampled_from(PRICED + UNPRICED), min_size=1, max_size=10),
       data=st.data())
def test_property_outcome_ignores_repeats_and_order(ids, data):
    # oracle: spec C1 — raises iff some id is unpriced, the message contains every unpriced
    # id; repeating ids or permuting them changes neither outcome.
    bad = sorted({i for i in ids if i in UNPRICED})
    kind, msg = _outcome(list(ids))
    assert kind == ("raise" if bad else "ok")
    for i in bad:
        assert i in msg
    shuffled = data.draw(st.permutations(ids))
    repeated = list(shuffled) + data.draw(st.lists(st.sampled_from(ids), max_size=5))
    for variant in (list(shuffled), repeated):
        k2, m2 = _outcome(variant)
        assert k2 == kind
        for i in bad:
            assert i in m2
    # Always also exercise a must-raise case, so a stub that never raises fails every example.
    k3, m3 = _outcome(list(ids) + ["zz-unpriced-delta"])
    assert k3 == "raise" and "zz-unpriced-delta" in m3


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_adding_priced_ids_keeps_the_unpriced_set_named():
    # oracle: spec C1. Relation: adding priced ids to an all-priced list keeps it passing,
    # adding them to a failing list keeps it failing and still names the same unpriced ids.
    base_ok = ["gpt-6-sol"]
    base_bad = ["zz-unpriced-alpha", "gpt-6-sol"]
    assert _outcome(base_ok) == ("ok", None)
    assert _outcome(base_ok + list(PRICED)) == ("ok", None)
    k1, m1 = _outcome(base_bad)
    k2, m2 = _outcome(list(PRICED) + base_bad + list(PRICED))
    assert k1 == k2 == "raise"
    assert "zz-unpriced-alpha" in m1 and "zz-unpriced-alpha" in m2
