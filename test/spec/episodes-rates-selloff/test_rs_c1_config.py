"""C1: the committed market_episodes endpoint lists the three sell-off episodes."""

from __future__ import annotations

from datetime import date

import pytest

# oracle: the spec's C1 table, copied verbatim.
EXPECTED = {
    "taper_tantrum": ("Taper tantrum", date(2013, 6, 18),
                      ["forward_guidance", "term_premium", "rates_vol", "discount_rate"]),
    "cpi_shock_2022": ("June 2022 CPI shock", date(2022, 6, 9),
                       ["inflation", "discount_rate", "rates_vol"]),
    "treasury_refunding_2023": ("Treasury refunding and Fitch downgrade", date(2023, 7, 31),
                                ["fiscal", "term_premium", "rates_vol"]),
}


@pytest.mark.criterion("C1")
def test_entries_exactly_as_specified(rs):
    # oracle: spec C1 table — one entry per id, exact name, anchor, tags; no entities.
    eps = rs.config_episodes()
    for eid, (name, anchor, tags) in EXPECTED.items():
        hits = [e for e in eps if e.get("id") == eid]
        assert len(hits) == 1, f"{len(hits)} entries with id {eid}"
        e = hits[0]
        assert e.get("name") == name, eid
        assert rs.as_date(e.get("anchor")) == anchor, eid
        assert e.get("factor_tags") == tags, eid
        assert e.get("entity_tags") == [], eid


@pytest.mark.criterion("C1")
def test_tags_are_vocabulary_tags(rs):
    # oracle: configs/ingest/vocabulary.yaml's factor_tags list. The new ids must exist.
    vocab = set(rs.vocabulary().get("factor_tags") or [])
    eps = {e.get("id"): e for e in rs.config_episodes()}
    for eid in EXPECTED:
        assert eid in eps, f"{eid} is not configured"
        assert set(eps[eid].get("factor_tags") or []) <= vocab, eid


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_existing_order_kept_and_anchors_ascending(rs):
    # oracle: spec C1. Relation: restricting the list to the ten existing ids gives their
    # committed order; adding the three new ids keeps ids unique and anchors ascending.
    eps = rs.config_episodes()
    ids = [e.get("id") for e in eps]
    assert set(EXPECTED) <= set(ids), sorted(set(EXPECTED) - set(ids))
    assert len(ids) == len(set(ids)), ids
    assert [i for i in ids if i in rs.GRADUATED_IDS] == rs.GRADUATED_IDS
    anchors = [rs.as_date(e.get("anchor")) for e in eps]
    assert all(a is not None for a in anchors), anchors
    assert anchors == sorted(anchors), list(zip(ids, anchors, strict=True))
