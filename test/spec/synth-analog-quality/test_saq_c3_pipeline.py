"""C3: QueryPipeline.synthesize runs check_analog_quality under numeric_validation."""

from __future__ import annotations

import json

import pytest

from api.models import synthesis_to_dict


def _synth(saq, root, *, numeric_validation, drop_invalid):
    pipe, s = saq.build_pipeline(root, saq.responder_for(saq.live_shock_vector()),
                                 numeric_validation=numeric_validation,
                                 drop_invalid=drop_invalid)
    assert s.synthesis.numeric_validation is numeric_validation
    assert s.synthesis.drop_invalid is drop_invalid
    syn = pipe.synthesize(saq.QUERY)
    retrieved = {c.chunk_id for c in syn.query.chunks}
    assert {saq.chunk_id(saq.GFSR), saq.chunk_id(saq.UAH)} <= retrieved
    body = synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
                             diagnostics=syn.diagnostics)
    return syn, json.loads(json.dumps(body))


def _codes(entries):
    return {w.get("code") for w in entries}


@pytest.mark.criterion("C3")
def test_live_case_annotated_in_response(saq, tmp_path):
    # oracle: the spec's live case applied by hand. IG_OAS 1 bp cites "more than
    # 1 percent" (percent->bp = 100; no quantity converts to ~1) -> unit_scale_mismatch.
    # USDTWD 5 pct analog, rationale "USD/UAH ...": UAH not in the USDTWD list ->
    # fx_proxy_unlisted (and "5 percent" -> pct factor 1, so no unit flag). Neither
    # is hard without drop_invalid, so both fields stay.
    syn, body = _synth(saq, tmp_path, numeric_validation=True, drop_invalid=False)
    sv = body["shock_vector"]
    assert set(sv) == {"IG_OAS", "USDTWD"}
    assert set(sv["IG_OAS"].get("validation", [])) == {saq.CODE_UNIT}
    assert set(sv["USDTWD"].get("validation", [])) == {saq.CODE_FX}
    diag = body["synthesis_diagnostics"]
    assert {(w.get("code"), w.get("field")) for w in diag["validation"]
            if w.get("code") in (saq.CODE_UNIT, saq.CODE_FX)} == {
        (saq.CODE_UNIT, "IG_OAS"), (saq.CODE_FX, "USDTWD")}
    assert [d for d in diag["dropped"] if d.get("stage") == "validate"] == []


@pytest.mark.criterion("C3")
def test_drop_invalid_removes_unit_slip_keeps_fx_proxy(saq, tmp_path):
    # oracle: C3 by hand. unit_scale_mismatch is hard: with drop_invalid IG_OAS is
    # removed and logged at stage "validate" with that code. fx_proxy_unlisted is
    # surface-only: USDTWD stays, still annotated.
    syn, body = _synth(saq, tmp_path, numeric_validation=True, drop_invalid=True)
    sv = body["shock_vector"]
    assert "IG_OAS" not in sv
    assert set(sv) == {"USDTWD"}
    assert set(sv["USDTWD"].get("validation", [])) == {saq.CODE_FX}
    drops = [d for d in body["synthesis_diagnostics"]["dropped"]
             if d.get("field") == "IG_OAS" and d.get("stage") == "validate"]
    assert len(drops) == 1
    assert saq.CODE_UNIT in drops[0].get("codes", [])
    assert "USDTWD" not in {d.get("field") for d in body["synthesis_diagnostics"]["dropped"]}


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_numeric_validation_switch_gates_both_codes(saq, tmp_path):
    # oracle: C3 metamorphic relation. Same corpus and scripted model, only
    # numeric_validation toggled: off -> neither code anywhere, both fields kept;
    # on -> both codes present (as worked by hand in the live-case test).
    _, off = _synth(saq, tmp_path / "off", numeric_validation=False, drop_invalid=True)
    _, on = _synth(saq, tmp_path / "on", numeric_validation=True, drop_invalid=False)
    ours = {saq.CODE_UNIT, saq.CODE_FX}
    assert set(off["shock_vector"]) == {"IG_OAS", "USDTWD"}
    for f in off["shock_vector"].values():
        assert not ours & set(f.get("validation", []))
    assert not ours & _codes(off["synthesis_diagnostics"]["validation"])
    assert set(on["shock_vector"]) == set(off["shock_vector"])
    assert _codes(on["synthesis_diagnostics"]["validation"]) >= ours
    assert ours <= {c for f in on["shock_vector"].values()
                    for c in f.get("validation", [])}
