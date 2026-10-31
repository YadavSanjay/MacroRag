"""C3: QueryPipeline.synthesize reports missing_fields and episode_mixed."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

FIELDS = ["UST_2Y", "UST_10Y", "USDJPY", "IG_OAS", "HY_OAS", "VIX", "MOVE"]


def _vector(svr) -> dict:
    # UST_10Y and USDJPY cite the tariff card, IG_OAS the DeepSeek card, HY_OAS the note;
    # VIX is emitted in an invalid unit (bp), so the synthesizer drops it.
    v: dict = {}
    for part in (svr.emitted("UST_10Y", -20.0, "bp", svr.TARIFF),
                 svr.emitted("USDJPY", -4.95, "pct", svr.TARIFF),
                 svr.emitted("IG_OAS", 4.0, "bp", svr.DEEPSEEK),
                 svr.emitted("HY_OAS", 30.0, "bp", svr.NOTE),
                 svr.emitted("VIX", 5.0, "bp", svr.TARIFF)):
        v.update(part)
    return v


@pytest.mark.criterion("C3")
def test_response_reports_missing_fields_and_mix(svr, tmp_path):
    # oracle: C1 + C2 by hand. Requested FIELDS; kept UST_10Y, USDJPY, IG_OAS, HY_OAS.
    # Missing: UST_2Y (not emitted, card field), VIX (dropped: invalid_unit), MOVE (not
    # emitted, not a card field). Two episodes cited -> episode_mixed on the three
    # card-citing fields, not on HY_OAS, and one warning.
    body = svr.run_pipeline(tmp_path, _vector(svr), fields=list(FIELDS))
    sv, diag = body["shock_vector"], body["synthesis_diagnostics"]
    assert set(sv) == {"UST_10Y", "USDJPY", "IG_OAS", "HY_OAS"}
    assert diag.get("missing_fields") == [
        {"field": "UST_2Y", "reason": "not_emitted", "codes": [], "card_field": True},
        {"field": "VIX", "reason": "dropped", "codes": ["invalid_unit"], "card_field": True},
        {"field": "MOVE", "reason": "not_emitted", "codes": [], "card_field": False},
    ]
    for n in ("UST_10Y", "USDJPY", "IG_OAS"):
        assert svr.CODE in sv[n].get("validation", []), n
    assert svr.CODE not in sv["HY_OAS"].get("validation", [])
    mixes = [w for w in diag["validation"] if w.get("code") == svr.CODE]
    assert mixes == [{"code": svr.CODE, "episodes": {
        "market_episode_tariff": ["USDJPY", "UST_10Y"],
        "market_episode_deepseek": ["IG_OAS"]}}]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_no_fields_and_one_episode(svr):
    # oracle: C3. Relation 1: without synthesis fields, missing_fields is [] (but the
    # key is present). Relation 2: re-citing IG_OAS to the tariff card (one episode)
    # removes every episode_mixed, while the two-episode run has them.
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        mixed = svr.run_pipeline(a, _vector(svr))
        single_vec = _vector(svr)
        single_vec["IG_OAS"]["chunk_ids"] = [svr.cid(svr.TARIFF)]
        single = svr.run_pipeline(b, single_vec)
    assert mixed["synthesis_diagnostics"].get("missing_fields") == []
    assert any(svr.CODE in f.get("validation", []) for f in mixed["shock_vector"].values())
    assert all(svr.CODE not in f.get("validation", [])
               for f in single["shock_vector"].values())
    assert [w for w in single["synthesis_diagnostics"]["validation"]
            if w.get("code") == svr.CODE] == []


@pytest.mark.criterion("C3")
@settings(max_examples=8, deadline=None)
@given(extra=st.lists(st.sampled_from(["UST_5Y", "UST_30Y", "MOVE_10Y", "USDTWD", "EQUITY_US"]),
                      unique=True, min_size=1))
def test_property_unemitted_requests_are_listed(svr, extra):
    # oracle: C1 computed in the test: every requested field the model never emitted is
    # listed as not_emitted, with card_field per the card-field set, after UST_2Y, VIX
    # and MOVE from the fixed request.
    with tempfile.TemporaryDirectory() as root:
        body = svr.run_pipeline(root, _vector(svr), fields=list(FIELDS) + list(extra))
    got = body["synthesis_diagnostics"].get("missing_fields")
    assert got == [
        {"field": "UST_2Y", "reason": "not_emitted", "codes": [], "card_field": True},
        {"field": "VIX", "reason": "dropped", "codes": ["invalid_unit"], "card_field": True},
        {"field": "MOVE", "reason": "not_emitted", "codes": [], "card_field": False},
    ] + [{"field": e, "reason": "not_emitted", "codes": [], "card_field": e in svr.CARD_FIELDS}
         for e in extra]
