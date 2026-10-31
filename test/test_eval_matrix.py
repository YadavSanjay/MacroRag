"""Phase 4: prompt × model regression matrix. Pure orchestrator (aggregate,
run_matrix, baseline round-trip, regression diff) + registry variant pinning.
Fully faked — no pipeline, no network."""

from __future__ import annotations

from eval.matrix import (
    LOWER_BETTER,
    MatrixCell,
    aggregate,
    cells_from,
    diff,
    format_diff,
    has_regression,
    load_baseline,
    run_matrix,
    save_baseline,
)
from llm.registry import PromptRegistry


def test_cells_from_is_cartesian_product():
    cells = cells_from(["base", "v2"], ["m1", "m2"])
    assert [c.key for c in cells] == ["base::m1", "base::m2", "v2::m1", "v2::m2"]


def test_aggregate_means_numeric_metrics_only():
    rows = [{"cell": "c", "faithfulness": 1.0, "n_details": 2, "case": "x"},
            {"cell": "c", "faithfulness": 0.0, "n_details": 4, "case": "y"}]
    agg = aggregate(rows)
    assert agg == {"faithfulness": 0.5, "n_details": 3.0}   # 'cell'/'case' skipped


def test_run_matrix_aggregates_per_cell():
    cases = [{"id": "a"}, {"id": "b"}]
    cells = [MatrixCell("base", "cheap"), MatrixCell("base", "strong")]

    def runner(case, variant, model):
        # 'strong' grounds better than 'cheap'
        return {"grounded": 3.0 if model == "strong" else 1.0}

    rep = run_matrix("synthesize", cases, cells, runner)
    assert rep["n_cases"] == 2
    assert rep["cells"]["base::cheap"]["grounded"] == 1.0
    assert rep["cells"]["base::strong"]["grounded"] == 3.0
    assert len(rep["rows"]) == 4


def test_baseline_roundtrip(tmp_path):
    report = {"task": "answer", "cells": {"base::m": {"faithfulness": 0.9}},
              "rows": [], "n_cases": 1}
    p = tmp_path / "base.json"
    save_baseline(report, p)
    loaded = load_baseline(p)
    assert loaded["cells"]["base::m"]["faithfulness"] == 0.9
    assert load_baseline(tmp_path / "missing.json") is None


def test_diff_flags_higher_better_regression():
    cur = {"cells": {"base::m": {"faithfulness": 0.80}}}
    base = {"cells": {"base::m": {"faithfulness": 0.95}}}
    d = diff(cur, base, tol=0.05)
    assert len(d) == 1 and d[0]["regression"] is True
    assert has_regression(d)


def test_diff_improvement_is_not_a_regression():
    cur = {"cells": {"base::m": {"faithfulness": 0.99}}}
    base = {"cells": {"base::m": {"faithfulness": 0.90}}}
    d = diff(cur, base, tol=0.05)
    assert d[0]["regression"] is False and not has_regression(d)


def test_diff_within_tolerance_is_not_a_regression():
    cur = {"cells": {"base::m": {"faithfulness": 0.92}}}
    base = {"cells": {"base::m": {"faithfulness": 0.95}}}    # -0.03, within tol
    assert not has_regression(diff(cur, base, tol=0.05))


def test_diff_lower_better_metric_direction():
    assert "n_unsupported" in LOWER_BETTER
    # more unsupported claims than baseline = regression
    cur = {"cells": {"base::m": {"n_unsupported": 3.0}}}
    base = {"cells": {"base::m": {"n_unsupported": 1.0}}}
    assert has_regression(diff(cur, base, tol=0.5))
    # fewer = improvement
    assert not has_regression(diff(base, cur, tol=0.5))


def test_diff_no_baseline_returns_empty():
    assert diff({"cells": {"base::m": {"x": 1.0}}}, None) == []
    assert "no baseline" in format_diff([])


# ── registry variant pinning (the matrix's variant axis) ─────────────────────

def _synthetic_registry(tmp_path) -> PromptRegistry:
    (tmp_path / "system").mkdir()
    (tmp_path / "system" / "strict-json.md").write_text("SYS")
    tdir = tmp_path / "tasks" / "demo"
    (tdir / "variants").mkdir(parents=True)
    (tdir / "base.md").write_text("BASE")
    (tdir / "base.meta.yaml").write_text("task: demo\nvariant_id: base\nsystem: strict-json\n")
    (tdir / "variants" / "openai.gpt-5.v2.md").write_text("V2")
    (tdir / "variants" / "openai.gpt-5.v2.meta.yaml").write_text(
        "task: demo\nvariant_id: openai.gpt-5.v2\nprovider: openai\n"
        "model_series: gpt-5\nversion: 2\nstatus: active\nsystem: strict-json\n")
    return PromptRegistry(tmp_path)


def test_set_pins_forces_variant_without_explicit_pin(tmp_path):
    reg = _synthetic_registry(tmp_path)
    assert reg.resolve("demo").variant_id == "base"           # default
    reg.set_pins({"demo": "openai.gpt-5.v2"})
    assert reg.resolve("demo").variant_id == "openai.gpt-5.v2"  # pinned
    assert reg.resolve("demo").user_template == "V2"
    # explicit pin= still overrides the global pin
    assert reg.resolve("demo", pin="base").variant_id == "base"
    reg.clear_pins()
    assert reg.resolve("demo").variant_id == "base"
    assert reg.get_pins() == {}
