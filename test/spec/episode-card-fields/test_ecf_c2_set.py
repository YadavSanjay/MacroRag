"""C2: EPISODE_CARD_FIELDS is exactly the shock fields episodes.CANONICAL names."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ingest.usstats.episodes import CANONICAL
from synthesis.diagnostics import EPISODE_CARD_FIELDS, missing_fields
from synthesis.vocab import SHOCK_FIELDS


def _canonical_fields() -> set[str]:
    return {field for field, _ in CANONICAL.values() if field is not None}


@pytest.mark.criterion("C2")
def test_set_equals_canonical_and_spec_literal(ecf):
    # oracle: spec C2 (the producer's own map is the source of truth) and the spec's
    # 11-name list; both new fields named in the Requirement must be members.
    assert set(EPISODE_CARD_FIELDS) == _canonical_fields()
    assert set(EPISODE_CARD_FIELDS) == set(ecf.CARD_FIELDS)
    assert set(ecf.NEW_FIELDS) <= set(EPISODE_CARD_FIELDS)
    assert set(EPISODE_CARD_FIELDS) <= set(SHOCK_FIELDS)
    # spec Notes: the vocabulary minus the card fields is exactly the 8 non-card names.
    assert set(SHOCK_FIELDS) - set(EPISODE_CARD_FIELDS) == set(ecf.NON_CARD_FIELDS)


@pytest.mark.criterion("C2")
@settings(max_examples=60, deadline=None)
@given(order=st.permutations(sorted(SHOCK_FIELDS)))
def test_property_card_flags_follow_canonical(order):
    # oracle: spec C2. Requesting the whole vocabulary in any order, the card_field flags
    # pick out exactly CANONICAL's non-None fields, and the set stays inside the vocabulary.
    got = missing_fields(list(order), [], [])
    assert [e["field"] for e in got] == list(order)
    assert {e["field"] for e in got if e["card_field"]} == _canonical_fields()
    assert set(EPISODE_CARD_FIELDS) <= set(SHOCK_FIELDS)
    assert set(EPISODE_CARD_FIELDS) == _canonical_fields()
