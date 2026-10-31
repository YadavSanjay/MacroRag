"""C4: the committed market_episodes endpoint configures the new episodes and horizons."""

from __future__ import annotations

from datetime import date

import pytest

# oracle: the spec's C4 table, copied verbatim.
EXPECTED = {
    "ai_peak_to_trough_2022": {
        "id": "ai_peak_to_trough_2022",
        "name": "2021-22 AI and growth-equity peak-to-trough",
        "anchor": date(2021, 11, 29), "horizons": [5, 10, 221],
        "factor_tags": ["equity_vol", "credit_spread", "discount_rate", "ai_capex"],
        "entity_tags": ["ai", "semiconductors"]},
    "hawkish_repricing_2022": {
        "id": "hawkish_repricing_2022",
        "name": "Aug-Oct 2022 hawkish real-rate repricing",
        "anchor": date(2022, 8, 16), "horizons": [5, 10, 42],
        "factor_tags": ["inflation", "discount_rate", "rates_vol"],
        "entity_tags": []},
    "ai_rotation_2024": {
        "id": "ai_rotation_2024",
        "name": "July 2024 rotation out of AI leaders",
        "anchor": date(2024, 7, 10), "horizons": [5, 10],
        "factor_tags": ["equity_vol", "valuation_stretch"],
        "entity_tags": ["ai", "semiconductors"]},
    "deepseek": {
        "id": "deepseek", "name": "DeepSeek AI selloff",
        "anchor": date(2025, 1, 24), "horizons": [1, 5, 10],
        "factor_tags": ["ai_capex", "hyperscaler_concentration", "valuation_stretch",
                        "equity_vol"],
        "entity_tags": ["ai", "semiconductors", "hyperscaler"]},
}
# The ids configured before this spec, in their committed order (tier3_usstats.yaml at
# the spec's base commit).
PREVIOUS_IDS = ["lehman", "us_downgrade", "taper_tantrum", "china_deval", "volmageddon",
                "covid", "russia_ukraine", "cpi_shock_2022", "svb",
                "treasury_refunding_2023", "yen_carry", "deepseek", "tariff"]


def _normalised(ech, e: dict) -> dict:
    """The entry with its anchor as a date (YAML may give a date or an ISO string)."""
    out = dict(e)
    if "anchor" in out:
        out["anchor"] = ech.as_date(out["anchor"])
    return out


@pytest.mark.criterion("C4")
def test_entries_exactly_as_specified(ech):
    # oracle: spec C4 table — one entry per id, equal to the row (tags in order).
    # ai_rotation_2024 may set horizons [5, 10] or omit the key; every horizon is an int.
    eps = ech.config_episodes()
    for eid, expected in EXPECTED.items():
        hits = [e for e in eps if e.get("id") == eid]
        assert len(hits) == 1, f"{len(hits)} entries with id {eid}"
        got = _normalised(ech, hits[0])
        if eid == "ai_rotation_2024" and "horizons" not in got:
            expected = {k: v for k, v in expected.items() if k != "horizons"}
        assert got == expected, eid
        assert all(type(h) is int for h in got.get("horizons", [])), (eid, got)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_restricting_to_previous_ids_gives_their_order(ech):
    # oracle: spec C4. Relation: deleting the three new episodes leaves the previously
    # configured ids in their committed order; with them, ids stay unique and anchors
    # ascending; only the deepseek and (optionally) ai_rotation_2024 entries set
    # horizons; every tag is a vocabulary tag.
    eps = ech.config_episodes()
    ids = [e.get("id") for e in eps]
    assert set(EXPECTED) <= set(ids), sorted(set(EXPECTED) - set(ids))
    assert len(ids) == len(set(ids)), ids
    assert [i for i in ids if i in PREVIOUS_IDS] == PREVIOUS_IDS
    anchors = [ech.as_date(e.get("anchor")) for e in eps]
    assert all(a is not None for a in anchors), anchors
    assert anchors == sorted(anchors), list(zip(ids, anchors, strict=True))
    allowed = {"ai_peak_to_trough_2022", "hawkish_repricing_2022", "ai_rotation_2024",
               "deepseek"}
    assert {e.get("id") for e in eps if "horizons" in e} <= allowed
    vocab = ech.vocabulary()
    factors = set(vocab.get("factor_tags") or [])
    entities = set(vocab.get("entity_tags") or [])
    for e in eps:
        assert set(e.get("factor_tags") or []) <= factors, e.get("id")
        assert set(e.get("entity_tags") or []) <= entities, e.get("id")
