"""C1: the committed market_episodes endpoint lists russia_ukraine, as specified."""

from __future__ import annotations

from datetime import date

import pytest


def _entry(ru) -> dict:
    hits = [e for e in ru.config_episodes() if e.get("id") == ru.EPISODE_ID]
    assert len(hits) == 1, f"{len(hits)} entries with id {ru.EPISODE_ID}"
    return hits[0]


@pytest.mark.criterion("C1")
def test_russia_ukraine_entry_is_exactly_as_specified(ru):
    # oracle: spec C1 — name, anchor 2022-02-23, factor tags in order, no entity tags.
    e = _entry(ru)
    assert e.get("name") == "Russian invasion of Ukraine"
    assert ru.as_date(e.get("anchor")) == date(2022, 2, 23)
    assert e.get("factor_tags") == ["geopolitical_risk", "sanctions_regime",
                                    "flight_to_quality", "credit_spread"]
    assert e.get("entity_tags") == []


@pytest.mark.criterion("C1")
def test_russia_ukraine_tags_are_vocabulary_tags(ru):
    # oracle: configs/ingest/vocabulary.yaml's factor_tags / entity_tags lists.
    vocab = ru.vocabulary()
    e = _entry(ru)
    assert set(e.get("factor_tags") or []) <= set(vocab.get("factor_tags") or [])
    assert set(e.get("entity_tags") or []) <= set(vocab.get("entity_tags") or [])


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_removing_russia_ukraine_leaves_the_graduated_list(ru):
    # oracle: spec C1 — the list is the nine market-episodes ids in their committed order
    # plus russia_ukraine, ids unique, anchors ascending. Relation: deleting the new
    # episode gives back the graduated list unchanged.
    eps = ru.config_episodes()
    ids = [e.get("id") for e in eps]
    assert ru.EPISODE_ID in ids, f"{ru.EPISODE_ID} is not configured"
    assert len(ids) == len(set(ids)), ids
    # Relaxed by spec episodes-rates-selloff (more episodes may be added): the nine
    # graduated ids keep their committed relative order.
    assert [i for i in ids if i in ru.GRADUATED_IDS] == ru.GRADUATED_IDS
    anchors = [ru.as_date(e.get("anchor")) for e in eps]
    assert all(a is not None for a in anchors), anchors
    assert anchors == sorted(anchors), list(zip(ids, anchors, strict=True))
