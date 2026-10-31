"""Shock-accuracy eval: pure scoring, the injected-synth runner, and the
store-derived reference (backtest against ingested DFAST/EBA paths)."""

from __future__ import annotations

from eval import shock_metrics
from eval.shock_eval import derive_reference_from_store, evaluate_shock_accuracy, load_shock_cases
from synthesis.schema import ShockField
from vectorstores.timeseries_store import TimeseriesStore

# ── pure scoring ──────────────────────────────────────────────────────────────

def test_score_field_present_within_tol():
    f = shock_metrics.score_field(ShockField("UST_10Y", -190, "bp", ["c1"]),
                                  {"value": -200, "unit": "bp", "tol": 100})
    assert f["present"] and f["sign_match"] and f["within_tol"]
    assert f["abs_error"] == 10 and f["unit_match"]


def test_score_field_sign_miss_out_of_tol():
    f = shock_metrics.score_field({"value": 150, "unit": "bp"},
                                  {"value": -200, "unit": "bp", "tol": 100})
    assert f["sign_match"] is False and f["within_tol"] is False
    assert f["abs_error"] == 350


def test_score_field_missing_is_not_within_tol():
    f = shock_metrics.score_field(None, {"value": 40, "unit": "pts", "tol": 20})
    assert f["present"] is False and f["within_tol"] is False
    assert f["sign_match"] is None and f["emitted"] is None


def test_score_field_unit_mismatch_never_within_tol():
    # right number, wrong unit -> not comparable, so not within tolerance
    f = shock_metrics.score_field({"value": 40, "unit": "pts"},
                                  {"value": 40, "unit": "rel", "tol": 5})
    assert f["unit_match"] is False and f["within_tol"] is False


def test_score_shock_vector_summary():
    emitted = {
        "UST_10Y": ShockField("UST_10Y", -190, "bp", ["c1"]),   # in tol, sign ok
        "VIX": ShockField("VIX", -5, "pts", ["c1"]),            # wrong sign, out of tol
        "MOVE": ShockField("MOVE", 0.3, "rel", ["c1"]),         # extra (not in ref)
    }
    reference = {
        "UST_10Y": {"value": -200, "unit": "bp", "tol": 100},
        "VIX": {"value": 40, "unit": "pts", "tol": 20},
        "IG_OAS": {"value": 250, "unit": "bp", "tol": 125},     # missing
    }
    out = shock_metrics.score_shock_vector(emitted, reference)
    s = out["summary"]
    assert s["n_reference"] == 3 and s["n_emitted"] == 2
    assert s["coverage"] == 2 / 3
    assert s["sign_accuracy"] == 0.5                            # UST ok, VIX wrong
    assert s["tolerance_rate"] == 1 / 3                         # only UST within tol
    assert s["extra_fields"] == ["MOVE"]


def test_aggregate_shock_skips_none():
    summaries = [
        {"coverage": 1.0, "sign_accuracy": 1.0, "tolerance_rate": 1.0,
         "mean_norm_error": 0.2, "mean_magnitude_ratio": 1.1},
        {"coverage": 0.0, "sign_accuracy": 0.0, "tolerance_rate": 0.0,
         "mean_norm_error": None, "mean_magnitude_ratio": None},   # emitted nothing
    ]
    agg = shock_metrics.aggregate_shock(summaries)
    assert agg["n_cases"] == 2
    assert agg["coverage"] == 0.5
    assert agg["mean_norm_error"] == 0.2                         # None skipped, not 0.1


# ── runner ─────────────────────────────────────────────────────────────────────

def test_evaluate_shock_accuracy_with_fake_synth():
    cases = [
        {"id": "case1", "question": "q1", "horizon_days": 180,
         "reference": {"UST_10Y": {"value": -200, "unit": "bp", "tol": 100},
                       "VIX": {"value": 40, "unit": "pts", "tol": 20}}},
        {"id": "no_ref", "question": "q2"},                       # skipped (no reference)
    ]
    seen = {}

    def synth_fn(question, *, horizon_days=None, base_filter=None):
        seen["horizon"] = horizon_days
        return {"UST_10Y": ShockField("UST_10Y", -210, "bp", ["c1"]),
                "VIX": ShockField("VIX", 45, "pts", ["c1"])}

    report = evaluate_shock_accuracy(synth_fn, cases)
    assert seen["horizon"] == 180                                # horizon threaded through
    assert len(report["rows"]) == 1                              # no-ref case skipped
    s = report["summary"]
    assert s["n_cases"] == 1 and s["coverage"] == 1.0 and s["sign_accuracy"] == 1.0
    assert s["tolerance_rate"] == 1.0


# ── store-derived reference (backtest against ingested paths) ──────────────────

def _dfast_store(tmp_path):
    store = TimeseriesStore(tmp_path / "ts.db")
    store.build([
        {"source": "DFAST_2026", "scenario": "baseline",
         "variable": "treasury_10y", "value": 4.0},
        {"source": "DFAST_2026", "scenario": "severely_adverse",
         "variable": "treasury_10y", "value": 2.0},
        {"source": "DFAST_2026", "scenario": "baseline", "variable": "vix", "value": 18},
        {"source": "DFAST_2026", "scenario": "severely_adverse",
         "variable": "vix", "value": 58},
        {"source": "DFAST_2026", "scenario": "baseline",
         "variable": "bbb_corporate_yield", "value": 5.5},
        {"source": "DFAST_2026", "scenario": "severely_adverse",
         "variable": "bbb_corporate_yield", "value": 9.0},
    ])
    return store


def test_derive_reference_from_store_level_and_spread(tmp_path):
    store = _dfast_store(tmp_path)
    ref = derive_reference_from_store(
        store, source_like="DFAST", baseline_scenario="baseline",
        adverse_scenario="severely_adverse")
    # level deltas
    assert ref["UST_10Y"]["value"] == -200 and ref["UST_10Y"]["unit"] == "bp"
    assert ref["VIX"]["value"] == 40 and ref["VIX"]["unit"] == "pts"
    # spread delta: (9.0-2.0) - (5.5-4.0) = 5.5 -> 550 bp
    assert ref["IG_OAS"]["value"] == 550
    # proportional tolerance = 0.5 * |delta|
    assert ref["UST_10Y"]["tol"] == 100 and ref["IG_OAS"]["tol"] == 275


def test_derive_reference_peak_beats_last(tmp_path):
    # a mean-reverting adverse path: vix spikes to 72 then settles at 28.
    store = TimeseriesStore(tmp_path / "ts.db")
    store.build([
        {"source": "DFAST_2026", "scenario": "baseline", "variable": "vix", "value": 22},
        {"source": "DFAST_2026", "scenario": "baseline", "variable": "vix", "value": 24},
        {"source": "DFAST_2026", "scenario": "severely_adverse", "variable": "vix", "value": 60},
        {"source": "DFAST_2026", "scenario": "severely_adverse", "variable": "vix", "value": 72},
        {"source": "DFAST_2026", "scenario": "severely_adverse", "variable": "vix", "value": 28},
    ])
    mapping = {"VIX": {"kind": "level", "variable": "vix", "unit": "pts", "scale": 1.0}}
    peak = derive_reference_from_store(store, source_like="DFAST",
                                       baseline_scenario="baseline",
                                       adverse_scenario="severely_adverse", mapping=mapping)
    last = derive_reference_from_store(store, source_like="DFAST",
                                       baseline_scenario="baseline",
                                       adverse_scenario="severely_adverse",
                                       mapping=mapping, reduce="last")
    assert peak["VIX"]["value"] == 49       # 72 - mean(22,24)=23
    assert last["VIX"]["value"] == 5        # 28 - 23  (tail understates the spike)


def test_derive_reference_peak_is_outlier_robust(tmp_path):
    # a steady ~+11 stress with one freak +70 print. Default p90 ignores the
    # outlier; percentile=100 recovers the raw absolute max.
    store = TimeseriesStore(tmp_path / "ts.db")
    rows = [{"source": "DFAST_2026", "scenario": "baseline",
             "variable": "vix", "value": 20}]
    for v in [30, 31, 30, 32, 31, 30, 31, 32, 30, 31, 90]:
        rows.append({"source": "DFAST_2026", "scenario": "severely_adverse",
                     "variable": "vix", "value": v})
    store.build(rows)
    mapping = {"VIX": {"kind": "level", "variable": "vix", "unit": "pts", "scale": 1.0}}
    robust = derive_reference_from_store(store, source_like="DFAST",
                                         baseline_scenario="baseline",
                                         adverse_scenario="severely_adverse",
                                         mapping=mapping)                 # p90 default
    raw = derive_reference_from_store(store, source_like="DFAST",
                                      baseline_scenario="baseline",
                                      adverse_scenario="severely_adverse",
                                      mapping=mapping, peak_percentile=100)
    assert robust["VIX"]["value"] == 12          # 90th pct of |dev|, outlier dropped
    assert raw["VIX"]["value"] == 70             # absolute max (the freak print)


def test_derive_reference_includes_ust_5y_by_default(tmp_path):
    store = TimeseriesStore(tmp_path / "ts.db")
    store.build([
        {"source": "DFAST_2026", "scenario": "baseline",
         "variable": "treasury_5y", "value": 3.8},
        {"source": "DFAST_2026", "scenario": "severely_adverse",
         "variable": "treasury_5y", "value": 1.3},
    ])
    ref = derive_reference_from_store(store, source_like="DFAST",
                                      baseline_scenario="baseline",
                                      adverse_scenario="severely_adverse")
    assert ref["UST_5Y"]["value"] == -250 and ref["UST_5Y"]["unit"] == "bp"


def test_evaluate_shock_accuracy_uses_store_derive_block(tmp_path):
    store = _dfast_store(tmp_path)
    cases = [{
        "id": "dfast", "question": "dfast severely adverse",
        "derive": {"source_like": "DFAST", "baseline_scenario": "baseline",
                   "adverse_scenario": "severely_adverse"},
        "reference": {"MOVE": {"value": 0.4, "unit": "rel", "tol": 0.3}},  # authored fallback
    }]

    def synth_fn(question, *, horizon_days=None, base_filter=None):
        return {"UST_10Y": ShockField("UST_10Y", -200, "bp", ["c1"]),
                "VIX": ShockField("VIX", 40, "pts", ["c1"]),
                "MOVE": ShockField("MOVE", 0.4, "rel", ["c1"])}

    report = evaluate_shock_accuracy(synth_fn, cases, store=store)
    row = report["rows"][0]
    # store-derived fields (UST_10Y, VIX, IG_OAS) merged with authored MOVE
    assert {"UST_10Y", "VIX", "IG_OAS", "MOVE"} <= set(row["fields"])
    assert report["summary"]["n_cases"] == 1


def test_evaluate_shock_accuracy_no_store_uses_authored_only(tmp_path):
    # same case, but WITHOUT a store -> only the authored MOVE reference is scored
    cases = [{"id": "dfast", "question": "q",
              "derive": {"source_like": "DFAST", "baseline_scenario": "baseline",
                         "adverse_scenario": "severely_adverse"},
              "reference": {"MOVE": {"value": 0.4, "unit": "rel", "tol": 0.3}}}]

    def synth_fn(q, *, horizon_days=None, base_filter=None):
        return {"MOVE": ShockField("MOVE", 0.4, "rel", ["c1"])}

    report = evaluate_shock_accuracy(synth_fn, cases)   # no store
    assert set(report["rows"][0]["fields"]) == {"MOVE"}


def test_derive_reference_skips_missing_variables(tmp_path):
    store = TimeseriesStore(tmp_path / "ts.db")
    store.build([{"source": "DFAST_2026", "scenario": "baseline",
                  "variable": "treasury_10y", "value": 4.0}])   # no adverse row
    ref = derive_reference_from_store(
        store, source_like="DFAST", baseline_scenario="baseline",
        adverse_scenario="severely_adverse")
    assert ref == {}                                            # nothing derivable


def test_derived_reference_scores_a_matching_vector(tmp_path):
    store = _dfast_store(tmp_path)
    ref = derive_reference_from_store(
        store, source_like="DFAST", baseline_scenario="baseline",
        adverse_scenario="severely_adverse")
    emitted = {"UST_10Y": ShockField("UST_10Y", -200, "bp", ["c1"]),
               "VIX": ShockField("VIX", 40, "pts", ["c1"]),
               "IG_OAS": ShockField("IG_OAS", 550, "bp", ["c1"])}
    s = shock_metrics.score_shock_vector(emitted, ref)["summary"]
    assert s["coverage"] == 1.0 and s["sign_accuracy"] == 1.0
    assert s["tolerance_rate"] == 1.0


# ── shipped reference file ─────────────────────────────────────────────────────

def test_shipped_shock_reference_loads():
    from utils.config import REPO_ROOT
    cases = load_shock_cases(REPO_ROOT / "configs" / "eval" / "shock_reference.yaml")
    assert len(cases) >= 3
    ids = {c["id"] for c in cases}
    assert "dfast_severely_adverse" in ids
    for c in cases:
        assert c["reference"] and "question" in c
