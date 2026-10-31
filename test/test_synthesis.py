"""Scenario-synthesis endpoint (AITrade contract): shock-field vocab, LLM
synthesizer validation, citation gate, pipeline.synthesize, filter normalization,
and the §8 Taiwan acceptance round-trip."""

from __future__ import annotations

import json

from llm.fake import FakeLLM
from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.gate import gate_shock_vector
from synthesis.schema import ShockField
from synthesis.synthesizer import synthesize_shock_vector
from synthesis.vocab import field_reference, is_valid_field, is_valid_unit
from utils.schema import RetrievedChunk

# ── vocabulary ────────────────────────────────────────────────────────────────

def test_vocab_fields_units_signs():
    assert is_valid_field("UST_10Y") and is_valid_field("USDTWD")
    assert not is_valid_field("AI_EQUITY")
    assert is_valid_unit("UST_10Y", "bp") and not is_valid_unit("UST_10Y", "pct")
    assert is_valid_unit("MOVE", "rel") and is_valid_unit("MOVE", "pts")
    assert is_valid_unit("VIX", "pts") and not is_valid_unit("VIX", "bp")
    ref = field_reference()
    assert "USDTWD" in ref and "TWD depreciates" in ref


# ── synthesizer validation ────────────────────────────────────────────────────

def _chunk(cid, rel="2026-06-10"):
    return RetrievedChunk(cid, f"text {cid}", 1.0,
                          {"release_date": rel, "publisher": "X", "publication": "Y"})


def test_synthesizer_keeps_valid_drops_invalid():
    chunks = [_chunk("c1")]
    payload = {"shock_vector": {
        "UST_10Y": {"value": -35, "unit": "bp", "chunk_ids": ["c1"], "rationale": "r"},
        "VIX": {"value": 18, "unit": "pts", "chunk_ids": ["c1"]},
        "BOGUS": {"value": 1, "unit": "bp", "chunk_ids": ["c1"]},      # unknown field
        "USDTWD": {"value": 8, "unit": "bp", "chunk_ids": ["c1"]},     # wrong unit
    }}
    out = synthesize_shock_vector(FakeLLM(responses=[json.dumps(payload)]),
                                  "q", chunks)
    assert set(out) == {"UST_10Y", "VIX"}
    assert out["UST_10Y"].value == -35.0 and out["UST_10Y"].unit == "bp"


def test_synthesizer_empty_when_no_chunks():
    assert synthesize_shock_vector(FakeLLM(responses=["{}"]), "q", []) == {}


def test_synthesizer_drops_zero_magnitude_fields():
    from synthesis.diagnostics import SynthesisDiagnostics
    chunks = [_chunk("c1")]
    payload = {"shock_vector": {
        "USDTWD": {"value": 5, "unit": "pct", "chunk_ids": ["c1"]},   # kept
        "VIX": {"value": 0, "unit": "pts", "chunk_ids": ["c1"]},      # padded 0 -> dropped
        "IG_OAS": {"value": 0.0, "unit": "bp", "chunk_ids": ["c1"]},  # dropped
    }}
    diag = SynthesisDiagnostics()
    out = synthesize_shock_vector(FakeLLM(responses=[json.dumps(payload)]),
                                  "q", chunks, diag=diag)
    assert set(out) == {"USDTWD"}
    zeroed = {d["field"] for d in diag.dropped if d["reason"] == "zero_magnitude"}
    assert zeroed == {"VIX", "IG_OAS"}


def test_synthesizer_fields_restrict_output():
    chunks = [_chunk("c1")]
    payload = {"shock_vector": {
        "UST_10Y": {"value": -35, "unit": "bp", "chunk_ids": ["c1"]},
        "VIX": {"value": 18, "unit": "pts", "chunk_ids": ["c1"]},
    }}
    out = synthesize_shock_vector(FakeLLM(responses=[json.dumps(payload)]),
                                  "q", chunks, fields=["vix"])   # case-insensitive
    assert set(out) == {"VIX"}


def test_synthesizer_horizon_renders_constraint():
    chunks = [_chunk("c1")]
    llm = FakeLLM(responses=[json.dumps({"shock_vector": {}})])
    synthesize_shock_vector(llm, "q", chunks, horizon_days=10)
    _system, user = llm.calls[-1]
    assert "10 day(s)" in user


# ── analog-transfer grounding (Priority 3) ────────────────────────────────────

def test_synthesizer_parses_grounding_mode():
    chunks = [_chunk("c1")]
    payload = {"shock_vector": {
        "UST_10Y": {"value": -30, "unit": "bp", "chunk_ids": ["c1"],
                    "grounding_mode": "analog"},
        "VIX": {"value": 10, "unit": "pts", "chunk_ids": ["c1"]},         # defaults direct
        "IG_OAS": {"value": 60, "unit": "bp", "chunk_ids": ["c1"],
                   "grounding_mode": "wishful"},                          # invalid -> direct
    }}
    out = synthesize_shock_vector(FakeLLM(responses=[json.dumps(payload)]), "q", chunks)
    assert out["UST_10Y"].grounding_mode == "analog"
    assert out["VIX"].grounding_mode == "direct"
    assert out["IG_OAS"].grounding_mode == "direct"


def test_shockfield_to_dict_carries_grounding_mode():
    d = ShockField("UST_10Y", -30, "bp", ["c1"], grounding_mode="analog").to_dict()
    assert d["grounding_mode"] == "analog"
    assert ShockField("VIX", 10, "pts", ["c1"]).to_dict()["grounding_mode"] == "direct"


def test_schema_dict_documents_grounding_modes():
    from synthesis.vocab import schema_dict
    modes = schema_dict()["grounding_modes"]
    assert set(modes) == {"direct", "analog"}


# ── citation gate ─────────────────────────────────────────────────────────────

def test_gate_requires_cited_chunk_present_and_dated():
    chunks = [_chunk("c1", "2026-06-10"), _chunk("c2", None)]   # c2 has no date
    fields = {
        "UST_10Y": ShockField("UST_10Y", -35, "bp", ["c1"]),     # ok
        "VIX": ShockField("VIX", 18, "pts", ["ghost"]),          # not in chunks
        "IG_OAS": ShockField("IG_OAS", 90, "bp", ["c2"]),        # cited chunk undated
    }
    kept = gate_shock_vector(fields, chunks)
    assert set(kept) == {"UST_10Y"}
    assert kept["UST_10Y"].chunk_ids == ["c1"]


# ── synthesis diagnostics (Priority 1 instrumentation) ────────────────────────

def test_diagnostics_outcome_transitions():
    assert SynthesisDiagnostics(skipped="no_chunks").outcome == "no_chunks"
    assert SynthesisDiagnostics(skipped="upstream_refused").outcome == "upstream_refused"
    assert SynthesisDiagnostics(emitted_fields=[]).outcome == "llm_empty"
    assert SynthesisDiagnostics(emitted_fields=["A"], kept_fields=[]).outcome == "all_dropped"
    assert SynthesisDiagnostics(emitted_fields=["A", "B"], kept_fields=["A"],
                                dropped=[{"field": "B"}]).outcome == "partial"
    assert SynthesisDiagnostics(emitted_fields=["A"], kept_fields=["A"]).outcome == "grounded"


def test_synthesizer_records_drop_reasons():
    chunks = [_chunk("c1")]
    payload = {"shock_vector": {
        "UST_10Y": {"value": -35, "unit": "bp", "chunk_ids": ["c1"]},   # kept
        "BOGUS": {"value": 1, "unit": "bp", "chunk_ids": ["c1"]},       # unknown_field
        "USDTWD": {"value": 8, "unit": "bp", "chunk_ids": ["c1"]},      # invalid_unit
        "VIX": {"value": "n/a", "unit": "pts", "chunk_ids": ["c1"]},    # non_numeric
    }}
    diag = SynthesisDiagnostics()
    out = synthesize_shock_vector(FakeLLM(responses=[json.dumps(payload)]),
                                  "q", chunks, diag=diag)
    assert set(out) == {"UST_10Y"}
    assert set(diag.emitted_fields) == {"UST_10Y", "BOGUS", "USDTWD", "VIX"}
    reasons = {d["field"]: d["reason"] for d in diag.dropped}
    assert reasons == {"BOGUS": "unknown_field", "USDTWD": "invalid_unit",
                       "VIX": "non_numeric"}


def test_synthesizer_diag_llm_empty():
    diag = SynthesisDiagnostics()
    out = synthesize_shock_vector(
        FakeLLM(responses=[json.dumps({"shock_vector": {}})]), "q",
        [_chunk("c1")], diag=diag)
    assert out == {} and diag.emitted_fields == [] and diag.outcome == "llm_empty"


def test_gate_records_no_dated_citation():
    chunks = [_chunk("c1", "2026-06-10"), _chunk("c2", None)]
    fields = {"UST_10Y": ShockField("UST_10Y", -35, "bp", ["c1"]),
              "IG_OAS": ShockField("IG_OAS", 90, "bp", ["c2"])}   # cited chunk undated
    diag = SynthesisDiagnostics()
    kept = gate_shock_vector(fields, chunks, diag=diag)
    assert set(kept) == {"UST_10Y"}
    drop = [d for d in diag.dropped if d["field"] == "IG_OAS"][0]
    assert drop["stage"] == "gate" and drop["reason"] == "no_dated_citation"
    assert drop["cited"] == 1 and drop["valid"] == 0


# ── filter normalization (spec §6) ────────────────────────────────────────────

def test_api_filter_normalizes_aitrade_tokens():
    from api.models import FilterModel
    mf = FilterModel(entity_tags=["taiwan", "china", "TSMC", "hyperscalers"],
                     factor_tags=["fx", "credit_spread"]).to_meta()
    assert "china" in mf.entity_tags and "semiconductors" in mf.entity_tags
    assert "hyperscaler" in mf.entity_tags and "taiwan" not in mf.entity_tags
    assert mf.factor_tags == ["fx", "credit_spread"]


# ── pipeline.synthesize + world_view_id ───────────────────────────────────────

def test_pipeline_synthesize_gated_with_world_view(indexed, tmp_path):
    from pipelines.query_pipeline import QueryPipeline
    from retriever.rerank import IdentityReranker
    from retriever.retriever import Retriever
    from utils.config import RagSettings, RetrievalSettings
    from utils.manifest import build_manifest, write_manifest
    from utils.trace import Tracer

    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=3))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    settings.paths.manifest = str(tmp_path / "manifest.json")
    write_manifest(settings.manifest_path, build_manifest(settings, chunks=1))

    synth = {"shock_vector": {
        "UST_10Y": {"value": -35, "unit": "bp", "chunk_ids": ["dfast_scen::000"]},
        "VIX": {"value": 18, "unit": "pts", "chunk_ids": ["ghost::999"]},  # gated out
    }}

    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps(synth)
        return '{"summary":"stress narrative","details":[],"unanswered":[]}'

    pipe = QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=None)
    res = pipe.synthesize("severely adverse unemployment stress test")
    assert set(res.shock_vector) == {"UST_10Y"}                # VIX gated (ghost id)
    assert res.shock_vector["UST_10Y"].value == -35.0
    assert res.world_view_id and res.world_view_id.startswith("macro-")
    assert res.query.answer.summary == "stress narrative"      # prose preserved

    # diagnostics explain the partial grounding (VIX dropped at the gate)
    diag = res.diagnostics
    assert diag.outcome == "partial"
    assert diag.kept_fields == ["UST_10Y"]
    assert diag.n_chunks >= 1 and diag.n_dated_chunks >= 1
    vix_drop = [d for d in diag.dropped if d["field"] == "VIX"][0]
    assert vix_drop["stage"] == "gate" and vix_drop["reason"] == "no_dated_citation"
    assert res.diagnostics.as_dict()["outcome"] == "partial"   # API-serializable
    assert diag.coverage_gap is False                          # a field grounded


# ── P4: config-wired gate knobs + graceful degradation ────────────────────────

def _synth_pipeline(indexed, tmp_path, responder, configure=None):
    from pipelines.query_pipeline import QueryPipeline
    from retriever.rerank import IdentityReranker
    from retriever.retriever import Retriever
    from utils.config import RagSettings, RetrievalSettings
    from utils.manifest import build_manifest, write_manifest
    from utils.trace import Tracer

    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=3))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    settings.paths.manifest = str(tmp_path / "manifest.json")
    if configure:
        configure(settings)
    write_manifest(settings.manifest_path, build_manifest(settings, chunks=1))
    return QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False), cache=None)


def _synth_responder(shock: dict):
    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": shock})
        return '{"summary":"stress narrative","details":[],"unanswered":[]}'
    return responder


def test_synthesis_config_defaults():
    from utils.config import RagSettings
    s = RagSettings().synthesis
    assert s.min_citation_chunks == 1 and s.enable_analog is True


def test_diagnostics_coverage_gap_flag():
    assert SynthesisDiagnostics(kept_fields=[]).coverage_gap is True
    assert SynthesisDiagnostics(kept_fields=["UST_10Y"]).coverage_gap is False
    assert SynthesisDiagnostics(kept_fields=[]).as_dict()["coverage_gap"] is True


def test_min_citation_chunks_config_tightens_gate(indexed, tmp_path):
    # one citation is fine by default; requiring 2 drops it -> coverage gap
    shock = {"UST_10Y": {"value": -35, "unit": "bp",
                         "chunk_ids": ["dfast_scen::000"]}}   # single dated citation
    pipe = _synth_pipeline(indexed, tmp_path, _synth_responder(shock),
                           configure=lambda s: setattr(s.synthesis,
                                                       "min_citation_chunks", 2))
    res = pipe.synthesize("severely adverse stress test")
    assert res.shock_vector == {}
    assert res.diagnostics.coverage_gap is True
    drop = [d for d in res.diagnostics.dropped if d["field"] == "UST_10Y"][0]
    assert drop["reason"] == "no_dated_citation" and drop["valid"] == 1


def test_enable_analog_false_drops_proxy_fields(indexed, tmp_path):
    shock = {"UST_10Y": {"value": -35, "unit": "bp",
                         "chunk_ids": ["dfast_scen::000"], "grounding_mode": "analog"}}
    pipe = _synth_pipeline(indexed, tmp_path, _synth_responder(shock),
                           configure=lambda s: setattr(s.synthesis,
                                                       "enable_analog", False))
    res = pipe.synthesize("severely adverse stress test")
    assert res.shock_vector == {}                       # analog dropped in strict mode
    drop = [d for d in res.diagnostics.dropped if d["field"] == "UST_10Y"][0]
    assert drop["stage"] == "config" and drop["reason"] == "analog_disabled"


# ── Step 1: numeric-validation / coherence pass ───────────────────────────────

from synthesis.validate import validate_shock_vector  # noqa: E402
from utils.schema import TimeseriesHit  # noqa: E402


def _sf(name, value, unit, **kw):
    return ShockField(name, value, unit, chunk_ids=["c1"], **kw)


def test_validate_flags_implausible_magnitude():
    diag = SynthesisDiagnostics()
    fields = {"UST_10Y": _sf("UST_10Y", 500, "bp"),      # cap 350 at ref horizon
              "VIX": _sf("VIX", 12, "pts")}              # well within 70
    validate_shock_vector(fields, diag=diag)
    assert "implausible_magnitude" in fields["UST_10Y"].validation
    assert fields["VIX"].validation == []
    codes = {w["code"] for w in diag.validation}
    assert "implausible_magnitude" in codes


def test_validate_horizon_scales_the_band():
    # 500bp breaches the 350bp UST_10Y cap at 90d, but a 360d horizon scales the
    # cap by sqrt(4)=2 -> 700bp, so the same move is now within band.
    fields = {"UST_10Y": _sf("UST_10Y", 500, "bp")}
    validate_shock_vector(fields, horizon_days=360, reference_horizon_days=90)
    assert fields["UST_10Y"].validation == []


def test_validate_series_anchor_exceeds_observed_range():
    # observed treasury_10y span = 1.0 pct -> 100bp; factor 3 -> 300bp ceiling.
    ts = [TimeseriesHit(source="DFAST_2026", publisher="Fed",
                        rows=[{"variable": "treasury_10y", "value": 4.0},
                              {"variable": "treasury_10y", "value": 5.0}])]
    over = {"UST_10Y": _sf("UST_10Y", 320, "bp")}
    validate_shock_vector(over, timeseries=ts)
    assert "exceeds_observed_range" in over["UST_10Y"].validation
    under = {"UST_10Y": _sf("UST_10Y", 250, "bp")}
    validate_shock_vector(under, timeseries=ts)
    assert under["UST_10Y"].validation == []


def test_validate_series_anchor_noop_without_matching_series():
    ts = [TimeseriesHit(source="SEP_2026", publisher="Fed",
                        rows=[{"variable": "unemployment_rate", "value": 4.0}])]
    fields = {"UST_10Y": _sf("UST_10Y", 300, "bp")}      # no treasury_10y rows
    validate_shock_vector(fields, timeseries=ts)
    assert fields["UST_10Y"].validation == []


def test_validate_coherence_credit_and_vol_divergence():
    diag = SynthesisDiagnostics()
    fields = {"IG_OAS": _sf("IG_OAS", 60, "bp"),
              "HY_OAS": _sf("HY_OAS", -80, "bp"),        # opposite sign to IG + VIX
              "VIX": _sf("VIX", 18, "pts")}
    validate_shock_vector(fields, diag=diag)
    assert "credit_indices_diverge" in fields["IG_OAS"].validation
    assert "credit_indices_diverge" in fields["HY_OAS"].validation
    assert "vol_credit_diverge" in fields["HY_OAS"].validation
    codes = {w["code"] for w in diag.validation}
    assert {"credit_indices_diverge", "vol_credit_diverge"} <= codes


def test_validate_coherence_same_sign_is_clean():
    fields = {"IG_OAS": _sf("IG_OAS", 60, "bp"), "HY_OAS": _sf("HY_OAS", 300, "bp"),
              "VIX": _sf("VIX", 18, "pts")}
    validate_shock_vector(fields)
    assert all(f.validation == [] for f in fields.values())


def test_validate_coherence_ignores_immaterial_moves():
    # a 2bp IG "move" is below the 5bp material floor -> no incoherence call.
    fields = {"IG_OAS": _sf("IG_OAS", 2, "bp"), "HY_OAS": _sf("HY_OAS", -300, "bp")}
    validate_shock_vector(fields)
    assert fields["IG_OAS"].validation == [] and fields["HY_OAS"].validation == []


def test_validate_drop_invalid_removes_only_hard_magnitude():
    diag = SynthesisDiagnostics()
    fields = {"UST_10Y": _sf("UST_10Y", 5000, "bp"),     # implausible -> hard drop
              "IG_OAS": _sf("IG_OAS", 60, "bp"),
              "HY_OAS": _sf("HY_OAS", -80, "bp")}         # incoherent but surface-only
    validate_shock_vector(fields, drop_invalid=True, diag=diag)
    assert "UST_10Y" not in fields                        # hard magnitude dropped
    assert {"IG_OAS", "HY_OAS"} <= set(fields)            # coherence never drops
    drop = [d for d in diag.dropped if d["field"] == "UST_10Y"][0]
    assert drop["stage"] == "validate" and drop["reason"] == "invalid_magnitude"


def test_shockfield_to_dict_carries_validation():
    f = _sf("UST_10Y", 500, "bp")
    assert "validation" not in f.to_dict()                # empty -> omitted
    validate_shock_vector({"UST_10Y": f})
    assert f.to_dict()["validation"] == ["implausible_magnitude"]


def test_synthesis_validation_config_defaults():
    from utils.config import RagSettings
    s = RagSettings().synthesis
    assert s.numeric_validation is True and s.drop_invalid is False
    assert s.validation_reference_horizon_days == 90
    assert s.reconcile_grounding is True


# ── grounding-mode reconciliation (analog -> direct backstop) ──────────────────

def _dfast_ts():
    def rows(scen, t10, vix):
        return [{"variable": "treasury_10y", "scenario": scen, "value": t10},
                {"variable": "vix", "scenario": scen, "value": vix}]
    return [TimeseriesHit(source="DFAST_2026", publisher="Fed",
                          rows=rows("baseline", 4.1, 23) + rows("severely_adverse", 2.3, 60))]


def test_reconcile_upgrades_analog_to_direct_when_data_backed_and_consistent():
    from synthesis.validate import reconcile_grounding_modes
    fields = {"UST_10Y": _sf("UST_10Y", -150, "bp", grounding_mode="analog"),  # yields fall
              "VIX": _sf("VIX", 40, "pts", grounding_mode="analog")}           # vol up
    reconcile_grounding_modes(fields, _dfast_ts())
    assert fields["UST_10Y"].grounding_mode == "direct"    # matches baseline->adverse dir
    assert fields["VIX"].grounding_mode == "direct"


def test_reconcile_leaves_analog_when_sign_contradicts_path():
    from synthesis.validate import reconcile_grounding_modes
    # DFAST 10y FALLS; an emitted +150 (rising) disagrees -> stays analog
    fields = {"UST_10Y": _sf("UST_10Y", 150, "bp", grounding_mode="analog")}
    reconcile_grounding_modes(fields, _dfast_ts())
    assert fields["UST_10Y"].grounding_mode == "analog"


def test_reconcile_leaves_analog_when_series_absent():
    from synthesis.validate import reconcile_grounding_modes
    # USDTWD has no backing series in the DFAST data -> untouched
    fields = {"USDTWD": _sf("USDTWD", 10, "pct", grounding_mode="analog")}
    reconcile_grounding_modes(fields, _dfast_ts())
    assert fields["USDTWD"].grounding_mode == "analog"


def test_reconcile_does_not_upgrade_hy_oas_no_true_series():
    from synthesis.validate import reconcile_grounding_modes
    # HY_OAS has no high-yield series (BBB is IG) -> a HY shock stays a proxy,
    # even though bbb_corporate_yield is present and sign-consistent.
    ts = [TimeseriesHit(source="DFAST_2026", publisher="Fed", rows=[
        {"variable": "bbb_corporate_yield", "scenario": "baseline", "value": 5.4},
        {"variable": "bbb_corporate_yield", "scenario": "severely_adverse", "value": 8.2}])]
    fields = {"HY_OAS": _sf("HY_OAS", 700, "bp", grounding_mode="analog")}   # widening
    reconcile_grounding_modes(fields, ts)
    assert fields["HY_OAS"].grounding_mode == "analog"


def test_reconcile_never_downgrades_direct():
    from synthesis.validate import reconcile_grounding_modes
    fields = {"UST_10Y": _sf("UST_10Y", 150, "bp", grounding_mode="direct")}
    reconcile_grounding_modes(fields, _dfast_ts())    # even sign-contradicting
    assert fields["UST_10Y"].grounding_mode == "direct"


def test_reconcile_noop_without_timeseries():
    from synthesis.validate import reconcile_grounding_modes
    fields = {"UST_10Y": _sf("UST_10Y", -150, "bp", grounding_mode="analog")}
    reconcile_grounding_modes(fields, None)
    assert fields["UST_10Y"].grounding_mode == "analog"


def test_pipeline_synthesize_surfaces_validation(indexed, tmp_path):
    # An implausible LLM-emitted magnitude is kept (surface-only default) but
    # flagged on the field and in the diagnostics.
    shock = {"UST_10Y": {"value": 5000, "unit": "bp",
                         "chunk_ids": ["dfast_scen::000"]}}
    pipe = _synth_pipeline(indexed, tmp_path, _synth_responder(shock))
    res = pipe.synthesize("severely adverse stress test")
    assert "UST_10Y" in res.shock_vector
    assert "implausible_magnitude" in res.shock_vector["UST_10Y"].validation
    codes = {w["code"] for w in res.diagnostics.as_dict()["validation"]}
    assert "implausible_magnitude" in codes


def test_pipeline_synthesize_drop_invalid_prunes(indexed, tmp_path):
    shock = {"UST_10Y": {"value": 5000, "unit": "bp", "chunk_ids": ["dfast_scen::000"]}}
    pipe = _synth_pipeline(indexed, tmp_path, _synth_responder(shock),
                           configure=lambda s: setattr(s.synthesis, "drop_invalid", True))
    res = pipe.synthesize("severely adverse stress test")
    assert res.shock_vector == {} and res.diagnostics.coverage_gap is True


# ── Step 2: rates-vol term structure ──────────────────────────────────────────

def test_vocab_has_rates_vol_term_structure():
    for name in ("MOVE_1Y", "MOVE_5Y", "MOVE_10Y", "MOVE_30Y"):
        assert is_valid_field(name)
        assert is_valid_unit(name, "rel") and is_valid_unit(name, "pts")
        assert not is_valid_unit(name, "bp")
    assert "MOVE_10Y" in field_reference()


def test_schema_dict_lists_term_structure_nodes():
    from synthesis.vocab import schema_dict
    names = {f["name"] for f in schema_dict()["fields"]}
    assert {"MOVE", "MOVE_1Y", "MOVE_5Y", "MOVE_10Y", "MOVE_30Y"} <= names


def test_synthesizer_accepts_term_structure_node():
    chunks = [_chunk("c1")]
    payload = {"shock_vector": {
        "MOVE_10Y": {"value": 0.4, "unit": "rel", "chunk_ids": ["c1"]},
    }}
    out = synthesize_shock_vector(FakeLLM(responses=[json.dumps(payload)]), "q", chunks)
    assert out["MOVE_10Y"].value == 0.4 and out["MOVE_10Y"].unit == "rel"


def test_validate_magnitude_covers_term_structure():
    fields = {"MOVE_10Y": _sf("MOVE_10Y", 5.0, "rel")}   # cap 3.0 rel
    validate_shock_vector(fields)
    assert "implausible_magnitude" in fields["MOVE_10Y"].validation


def test_validate_rates_vol_term_inconsistency():
    diag = SynthesisDiagnostics()
    fields = {"MOVE": _sf("MOVE", 0.30, "rel"),
              "MOVE_5Y": _sf("MOVE_5Y", 0.25, "rel"),
              "MOVE_30Y": _sf("MOVE_30Y", -0.30, "rel")}   # minority: fights the curve
    validate_shock_vector(fields, diag=diag)
    assert "rates_vol_term_inconsistent" in fields["MOVE_30Y"].validation
    assert fields["MOVE"].validation == [] and fields["MOVE_5Y"].validation == []
    assert any(w["code"] == "rates_vol_term_inconsistent" for w in diag.validation)


def test_validate_rates_vol_term_all_same_sign_is_clean():
    fields = {"MOVE": _sf("MOVE", 0.30, "rel"), "MOVE_10Y": _sf("MOVE_10Y", 0.40, "rel")}
    validate_shock_vector(fields)
    assert all(f.validation == [] for f in fields.values())


def test_validate_rates_vol_term_single_node_noop():
    fields = {"MOVE_10Y": _sf("MOVE_10Y", -0.40, "rel")}   # <2 nodes -> nothing to compare
    validate_shock_vector(fields)
    assert fields["MOVE_10Y"].validation == []


# ── EQUITY_US (D4): a level field, so it moves OPPOSITE to vol and spreads ────

def test_equity_us_is_in_the_controlled_vocab():
    from synthesis.vocab import SHOCK_FIELDS, is_valid_field, is_valid_unit
    assert is_valid_field("EQUITY_US") and is_valid_unit("EQUITY_US", "pct")
    assert not is_valid_unit("EQUITY_US", "bp")
    assert SHOCK_FIELDS["EQUITY_US"].positive_means == "equity index rises"


def test_equity_and_vol_rising_together_is_incoherent():
    diag = SynthesisDiagnostics()
    fields = {"EQUITY_US": _sf("EQUITY_US", 12, "pct"),   # equities up...
              "VIX": _sf("VIX", 20, "pts")}               # ...and vol up: two worlds
    validate_shock_vector(fields, diag=diag)
    assert "equity_vol_diverge" in fields["EQUITY_US"].validation
    assert "equity_vol_diverge" in fields["VIX"].validation
    assert "equity_vol_diverge" in {w["code"] for w in diag.validation}


def test_equity_down_with_vol_up_is_clean():
    fields = {"EQUITY_US": _sf("EQUITY_US", -25, "pct"),
              "VIX": _sf("VIX", 30, "pts"),
              "HY_OAS": _sf("HY_OAS", 400, "bp")}
    validate_shock_vector(fields)
    assert all(f.validation == [] for f in fields.values())


def test_equity_rising_with_credit_widening_is_flagged_softly():
    diag = SynthesisDiagnostics()
    fields = {"EQUITY_US": _sf("EQUITY_US", 15, "pct"),
              "HY_OAS": _sf("HY_OAS", 300, "bp")}
    validate_shock_vector(fields, diag=diag)
    assert "equity_credit_diverge" in fields["EQUITY_US"].validation
    soft = [w for w in diag.validation if w["code"] == "equity_credit_diverge"]
    assert soft and soft[0]["hard"] is False


def test_equity_implausible_magnitude_is_flagged():
    fields = {"EQUITY_US": _sf("EQUITY_US", -95, "pct")}   # beyond any realized move
    validate_shock_vector(fields)
    assert "implausible_magnitude" in fields["EQUITY_US"].validation
