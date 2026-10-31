"""C7: every pipeline LLM call runs under the tier of the setting that supplied its model."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _purposes(log) -> set[str]:
    return {p for p, _, _ in log}


def _assert_tiers(lob, log, route, llm):
    """Every logged call ran under expected_tier(purpose, model, route) (Requirement)."""
    assert log, "no LLM calls were made"
    got = [(p, m, t) for p, m, t in log]
    want = [(p, m, lob.expected_tier(p, m, route, llm)) for p, m, _ in log]
    assert got == want


@pytest.mark.criterion("C7")
def test_simple_route_answer(lob, tmp_path):
    # oracle: spec Requirement "Call sites set the tier": `plan` for planning and verify;
    # `answer` for generate on a route (DIRECT) not in llm.complex_routes.
    llm = lob.TierRecorder(route="DIRECT")
    s = lob.pipeline_settings()
    lob.build(tmp_path, llm, s).answer(lob.QUERY)
    assert {"plan", "generate", "verify"} <= _purposes(llm.log)
    assert [t for p, _, t in llm.log if p == "generate"] == ["answer"]
    assert {t for p, _, t in llm.log if p in ("plan", "verify")} == {"plan"}
    _assert_tiers(lob, llm.log, "DIRECT", s.llm)


@pytest.mark.criterion("C7")
def test_complex_route_answer(lob, tmp_path):
    # oracle: spec Requirement: generate on a route in llm.complex_routes (DECOMPOSE) runs
    # under `complex`; planning and verify stay `plan`.
    llm = lob.TierRecorder(route="DECOMPOSE")
    s = lob.pipeline_settings()
    assert "DECOMPOSE" in s.llm.complex_routes
    lob.build(tmp_path, llm, s).answer(lob.QUERY)
    assert [t for p, _, t in llm.log if p == "generate"] == ["complex"]
    _assert_tiers(lob, llm.log, "DECOMPOSE", s.llm)


@pytest.mark.criterion("C7")
def test_synthesis_and_factor_discovery(lob, tmp_path):
    # oracle: spec Requirement: `complex` for synthesize and discover_factors; the answer
    # step inside synthesize keeps its own tiers.
    llm = lob.TierRecorder(route="DIRECT")
    s = lob.pipeline_settings()
    pipe = lob.build(tmp_path, llm, s)
    pipe.synthesize(lob.QUERY)
    assert [t for p, _, t in llm.log if p == "synthesize"] == ["complex"]
    _assert_tiers(lob, llm.log, "DIRECT", s.llm)
    llm.log.clear()
    pipe.discover_factors("US rates portfolio")
    assert [t for p, _, t in llm.log if p == "discover_factors"] == ["complex"]
    _assert_tiers(lob, llm.log, "DIRECT", s.llm)


@pytest.mark.criterion("C7")
def test_prose_escalation_answer_to_complex(lob, tmp_path):
    # oracle: spec Requirement: an escalated re-run whose model is llm.complex_model runs
    # under `complex`. The double's verifier drops every claim, so the enabled escalation
    # re-generates the simple-route answer on complex_model (answer -> complex).
    llm = lob.TierRecorder(route="DIRECT")
    s = lob.pipeline_settings(escalation=True)
    lob.build(tmp_path, llm, s).answer(lob.QUERY)
    esc = [(m, t) for p, m, t in llm.log if p == "generate:escalated"]
    assert esc == [(s.llm.complex_model, "complex")]
    _assert_tiers(lob, llm.log, "DIRECT", s.llm)


@pytest.mark.criterion("C7")
def test_escalation_above_complex_and_multi_hop(lob, tmp_path):
    # oracle: spec Requirement: an escalated re-run on a model other than complex_model runs
    # under `escalation` (complex route and synthesis, escalation_model set); multi_hop
    # runs under `plan`.
    llm = lob.TierRecorder(route="DECOMPOSE")
    s = lob.pipeline_settings(escalation=True, escalation_model="gpt-6-sol")
    lob.build(tmp_path / "a", llm, s).synthesize(lob.QUERY)
    assert [t for p, _, t in llm.log if p == "generate:escalated"] == ["escalation"]
    assert [t for p, _, t in llm.log if p == "synthesize:escalated"] == ["escalation"]
    _assert_tiers(lob, llm.log, "DECOMPOSE", s.llm)
    hop = lob.TierRecorder(route="DIRECT")
    s2 = lob.pipeline_settings(multi_hop=2)
    lob.build(tmp_path / "b", hop, s2).answer(lob.QUERY)
    assert [t for p, _, t in hop.log if p == "multi_hop"] == ["plan"]


@pytest.mark.criterion("C7")
@settings(max_examples=12, deadline=None)
@given(route=st.sampled_from(["DIRECT", "PARAPHRASE", "DECOMPOSE",
                              "DECOMPOSE_AND_PARAPHRASE"]),
       escalation=st.booleans(), verify=st.booleans(),
       esc_model=st.sampled_from([None, "gpt-6-sol"]), synth=st.booleans())
def test_property_every_call_has_its_tier(lob, route, escalation, verify, esc_model, synth):
    # oracle: spec Requirement "Call sites set the tier", computed per call by
    # expected_tier (conftest) from the purpose, the model and the route.
    llm = lob.TierRecorder(route=route)
    s = lob.pipeline_settings(escalation=escalation, verify=verify,
                              escalation_model=esc_model)
    with tempfile.TemporaryDirectory() as root:
        pipe = lob.build(root, llm, s)
        if synth:
            pipe.synthesize(lob.QUERY)
        else:
            pipe.answer(lob.QUERY)
    assert "generate" in _purposes(llm.log)
    _assert_tiers(lob, llm.log, route, s.llm)
