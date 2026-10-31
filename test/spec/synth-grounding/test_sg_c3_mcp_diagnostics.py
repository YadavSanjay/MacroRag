"""C3: MCP macro_synthesize_shock_vector returns the REST synthesize body, diagnostics included."""

from __future__ import annotations

import asyncio
import json
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from api.models import synthesis_to_dict
from mcp_server.server import build_mcp

# No whole-word vocabulary cue and no year: the query sets no facet filter, so all
# four (dated) corpus chunks are retrieved (final_k=10 > 4).
QUERY = "how would markets move under this shock"


def _responder(shock_vector: dict):
    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": shock_vector})
        return json.dumps({"summary": "scenario narrative", "details": [],
                           "unanswered": []})
    return responder


def _mcp_body(pipe, settings_, args):
    mcp = build_mcp(lambda: pipe, settings_)
    blocks = asyncio.run(mcp.call_tool("macro_synthesize_shock_vector", args))
    return json.loads(blocks[0].text)


def _rest_body(pipe, question):
    syn = pipe.synthesize(question)
    body = synthesis_to_dict(syn.query, syn.shock_vector, syn.world_view_id,
                             diagnostics=syn.diagnostics)
    return json.loads(json.dumps(body))      # same JSON normalisation as the wire


@pytest.mark.criterion("C3")
def test_empty_model_vector_reports_llm_empty_over_mcp(tmp_path, make_pipeline):
    # oracle: SynthesisDiagnostics.outcome semantics (synthesis/diagnostics.py):
    # chunks were retrieved and the model emitted no field -> "llm_empty"; no kept
    # field -> coverage_gap True. Body equality: REST serialization of the same
    # SynthesisResult (api.models.synthesis_to_dict with diagnostics=).
    pipe, s = make_pipeline(tmp_path, _responder({}))
    body = _mcp_body(pipe, s, {"question": QUERY})
    assert len(body["chunks"]) == 4
    assert body["shock_vector"] == {}
    assert "synthesis_diagnostics" in body
    assert body["synthesis_diagnostics"]["outcome"] == "llm_empty"
    assert body["synthesis_diagnostics"]["coverage_gap"] is True
    # Relaxed by spec answer-usage-cost: the MCP body also carries `usage`, pinned there (C5).
    assert {k: v for k, v in body.items() if k != "usage"} == _rest_body(pipe, QUERY)


@pytest.mark.criterion("C3")
def test_grounded_field_reports_grounded_over_mcp(tmp_path, make_pipeline, sg):
    # oracle: SynthesisDiagnostics.outcome semantics: one emitted field (UST_10Y,
    # -35 bp, a valid vocab field/unit, grounding_mode "direct") citing a dated
    # retrieved chunk survives the gate with nothing dropped -> "grounded",
    # coverage_gap False (numeric validation is off in the fixture). Body equality
    # with the REST serialization of the same SynthesisResult.
    cite = sg.chunk_id(sg.EBA)
    pipe, s = make_pipeline(tmp_path, _responder(
        {"UST_10Y": {"value": -35, "unit": "bp", "chunk_ids": [cite],
                     "grounding_mode": "direct"}}))
    body = _mcp_body(pipe, s, {"question": QUERY})
    assert cite in {c["chunk_id"] for c in body["chunks"]}
    assert set(body["shock_vector"]) == {"UST_10Y"}
    assert "synthesis_diagnostics" in body
    assert body["synthesis_diagnostics"]["outcome"] == "grounded"
    assert body["synthesis_diagnostics"]["coverage_gap"] is False
    # Relaxed by spec answer-usage-cost: the MCP body also carries `usage`, pinned there (C5).
    assert {k: v for k, v in body.items() if k != "usage"} == _rest_body(pipe, QUERY)


@pytest.mark.criterion("C3")
@settings(max_examples=25)
@given(emit=st.booleans(),
       bp=st.integers(min_value=-60, max_value=60).filter(lambda v: v != 0),
       doc=st.sampled_from(["eba_adverse", "imf_gfsr_ch2", "taiwan_semis",
                            "equity_vol_note"]))
def test_mcp_body_equals_rest_serialization(make_pipeline, emit, bp, doc):
    # oracle: C3 states the MCP body equals synthesis_to_dict(..., diagnostics=
    # syn.diagnostics) for the same deterministic pipeline (NullCache, semantic
    # cache off, verify off), whatever the model emits; and outcome follows the
    # diagnostics semantics: nothing emitted -> llm_empty / coverage_gap True,
    # one valid cited field -> grounded / coverage_gap False.
    vec = ({"UST_10Y": {"value": bp, "unit": "bp", "chunk_ids": [f"{doc}::000"],
                        "grounding_mode": "direct"}}
           if emit else {})
    with tempfile.TemporaryDirectory() as d:
        pipe, s = make_pipeline(d, _responder(vec))
        body = _mcp_body(pipe, s, {"question": QUERY})
        rest = _rest_body(pipe, QUERY)
    assert "synthesis_diagnostics" in body
    # Relaxed by spec answer-usage-cost: the MCP body also carries `usage`, pinned there (C5).
    assert {k: v for k, v in body.items() if k != "usage"} == rest
    diag = body["synthesis_diagnostics"]
    assert diag["outcome"] == ("grounded" if emit else "llm_empty")
    assert diag["coverage_gap"] is (not emit)
