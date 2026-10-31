"""C3: QueryPipeline.synthesize runs check_source_horizon before drop_invalid."""

from __future__ import annotations

import json

import pytest

from api.models import synthesis_to_dict

MISMATCH = "horizon_mismatch"
UNKNOWN = "horizon_unknown"
ALL = {"IG_OAS", "UST_2Y", "UST_10Y", "UST_5Y", "UST_30Y"}


def _synth(sah, root, *, horizon_days=10, **cfg):
    pipe, s = sah.build_pipeline(root, sah.responder_for(sah.live_shock_vector()), **cfg)
    for key, val in cfg.items():
        assert getattr(s.synthesis, key) == val
    syn = pipe.synthesize(sah.QUERY, horizon_days=horizon_days)
    retrieved = {c.chunk_id for c in syn.query.chunks}
    assert {sah.chunk_id(d) for d in (sah.DFAST, sah.TBAC, sah.NOTE)} <= retrieved
    body = synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
                             diagnostics=syn.diagnostics)
    return json.loads(json.dumps(body))


def _hcodes(field: dict) -> set[str]:
    return {c for c in field.get("validation", []) if c in (MISMATCH, UNKNOWN)}


def _diag_pairs(body) -> set[tuple[str, str]]:
    return {(w.get("code"), w.get("field"))
            for w in body["synthesis_diagnostics"]["validation"]
            if w.get("code") in (MISMATCH, UNKNOWN)}


@pytest.mark.criterion("C3")
def test_live_case_annotated_in_response(sah, tmp_path):
    # oracle: the spec's rules by hand at H = 10, T = 3.
    # IG_OAS and UST_2Y cite the dfast_scenarios chunk: m = 90 > 30 -> mismatch.
    # UST_10Y cites the TBAC chunk (doc_type tbac_presentation: unrated), no d -> unknown.
    # UST_5Y cites an unrated note, d = 10: ratio 1 -> no code.
    # UST_30Y cites TBAC, d = 90: 90/10 = 9 > 3 -> mismatch.
    # Without drop_invalid all five fields stay.
    body = _synth(sah, tmp_path, numeric_validation=True, drop_invalid=False)
    sv = body["shock_vector"]
    assert set(sv) == ALL
    assert _hcodes(sv["IG_OAS"]) == {MISMATCH}
    assert _hcodes(sv["UST_2Y"]) == {MISMATCH}
    assert _hcodes(sv["UST_10Y"]) == {UNKNOWN}
    assert _hcodes(sv["UST_5Y"]) == set()
    assert _hcodes(sv["UST_30Y"]) == {MISMATCH}
    assert _diag_pairs(body) == {(MISMATCH, "IG_OAS"), (MISMATCH, "UST_2Y"),
                                 (UNKNOWN, "UST_10Y"), (MISMATCH, "UST_30Y")}


@pytest.mark.criterion("C3")
def test_drop_invalid_removes_mismatch_keeps_unknown(sah, tmp_path):
    # oracle: C3 by hand. horizon_mismatch is hard: with drop_invalid, IG_OAS, UST_2Y
    # and UST_30Y are removed, each with one stage "validate" drop whose codes include
    # horizon_mismatch. horizon_unknown is surface-only: UST_10Y stays, annotated;
    # UST_5Y (no code) stays.
    body = _synth(sah, tmp_path, numeric_validation=True, drop_invalid=True)
    sv = body["shock_vector"]
    assert set(sv) == {"UST_10Y", "UST_5Y"}
    assert _hcodes(sv["UST_10Y"]) == {UNKNOWN}
    dropped = body["synthesis_diagnostics"]["dropped"]
    for name in ("IG_OAS", "UST_2Y", "UST_30Y"):
        drops = [d for d in dropped
                 if d.get("field") == name and d.get("stage") == "validate"]
        assert len(drops) == 1
        assert MISMATCH in drops[0].get("codes", [])
    assert not {"UST_10Y", "UST_5Y"} & {d.get("field") for d in dropped}


@pytest.mark.criterion("C3")
def test_tolerance_comes_from_settings(sah, tmp_path):
    # oracle: C3 "tolerance from synthesis.horizon_tolerance", by hand with T = 10:
    # dfast m = 90: H*T = 100 >= 90 -> compatible (IG_OAS, UST_2Y no code);
    # UST_30Y d = 90: 9 <= 10 -> no code; UST_10Y still unknown (no rating, no d).
    body = _synth(sah, tmp_path, numeric_validation=True, drop_invalid=True,
                  horizon_tolerance=10.0)
    sv = body["shock_vector"]
    assert {"UST_2Y", "UST_10Y", "UST_5Y", "UST_30Y"} <= set(sv)
    assert _hcodes(sv["UST_10Y"]) == {UNKNOWN}
    for name in ("UST_2Y", "UST_5Y", "UST_30Y"):
        assert _hcodes(sv[name]) == set()
    assert _diag_pairs(body) == {(UNKNOWN, "UST_10Y")}
    assert not any(MISMATCH in d.get("codes", [])
                   for d in body["synthesis_diagnostics"]["dropped"])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_switches_gate_both_codes(sah, tmp_path):
    # oracle: C3 metamorphic relation. Same corpus and scripted model; with
    # horizon_check off, numeric_validation off, or no horizon_days, neither code
    # appears anywhere (and nothing is dropped for it); with all on, both appear.
    on = _synth(sah, tmp_path / "on", numeric_validation=True, drop_invalid=False)
    assert {c for f in on["shock_vector"].values() for c in _hcodes(f)} == {
        MISMATCH, UNKNOWN}
    variants = [
        ("hc_off", {"horizon_check": False}, 10),
        ("nv_off", {"numeric_validation": False}, 10),
        ("no_h", {}, None),
    ]
    for tag, cfg, h in variants:
        cfg = {"numeric_validation": True, "drop_invalid": True, **cfg}
        off = _synth(sah, tmp_path / tag, horizon_days=h, **cfg)
        assert {"UST_2Y", "UST_10Y", "UST_5Y", "UST_30Y"} <= set(off["shock_vector"])
        for f in off["shock_vector"].values():
            assert _hcodes(f) == set()
        assert _diag_pairs(off) == set()
        assert not any({MISMATCH, UNKNOWN} & set(d.get("codes", []))
                       for d in off["synthesis_diagnostics"]["dropped"])
