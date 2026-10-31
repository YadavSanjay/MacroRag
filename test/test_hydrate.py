"""Time-series hydration + the compact scenario-path DATA rendering.

Regression cover for the DFAST numeric-extraction bug: a multi-factor scenario
query must hydrate EVERY named variable (not just the first cue match), and
format_data must keep each variable's baseline+adverse paths visible (not truncate
them out behind a global row cap)."""

from __future__ import annotations

from llm.generate import format_data
from pipelines.hydrate import _matched_variables, hydrate_timeseries
from utils.schema import TimeseriesHit
from vectorstores.timeseries_store import TimeseriesStore


def test_matched_variables_multi_factor_query():
    q = "shocks to Treasury yields, equity volatility, and IG/HY credit spreads"
    vs = _matched_variables(q)
    assert "treasury_5y" in vs and "treasury_10y" in vs   # "treasury"
    assert "vix" in vs                                     # "volatility"
    assert "bbb_corporate_yield" in vs                     # "credit spread"


def _dfast_store(tmp_path):
    store = TimeseriesStore(tmp_path / "ts.db")
    rows = []
    for scen, t10, vix, bbb in [("baseline", 4.1, 23, 5.4),
                                ("severely_adverse", 2.3, 72, 8.2)]:
        rows += [
            {"source": "DFAST_2026", "publisher": "Fed", "scenario": scen,
             "variable": "treasury_10y", "value": t10},
            {"source": "DFAST_2026", "publisher": "Fed", "scenario": scen,
             "variable": "vix", "value": vix},
            {"source": "DFAST_2026", "publisher": "Fed", "scenario": scen,
             "variable": "bbb_corporate_yield", "value": bbb},
        ]
    store.build(rows)
    return store


def test_hydrate_pulls_every_named_variable(tmp_path):
    store = _dfast_store(tmp_path)
    q = ("Under the DFAST severely adverse scenario, what are the shocks to "
         "Treasury yields, equity volatility, and IG/HY credit spreads?")
    hits = hydrate_timeseries(store, q)
    assert len(hits) == 1
    got = {r["variable"] for r in hits[0].rows}
    assert {"treasury_10y", "vix", "bbb_corporate_yield"} <= got   # not just vix


def test_hydrate_no_variable_cue_pulls_whole_source(tmp_path):
    store = _dfast_store(tmp_path)
    hits = hydrate_timeseries(store, "DFAST severely adverse scenario paths")
    # no variable keyword -> whole-source pull (every variable present)
    assert len(hits) == 1
    assert {"treasury_10y", "vix", "bbb_corporate_yield"} <= {
        r["variable"] for r in hits[0].rows}


def test_format_data_groups_by_variable_and_scenario():
    hit = TimeseriesHit(source="DFAST_2026", publisher="Fed", rows=[
        {"variable": "vix", "scenario": "baseline", "value": 23},
        {"variable": "vix", "scenario": "baseline", "value": 24},
        {"variable": "vix", "scenario": "severely_adverse", "value": 72},
        {"variable": "vix", "scenario": "severely_adverse", "value": 28},
    ])
    out = format_data([hit])
    assert "vix [baseline]:" in out and "vix [severely_adverse]:" in out
    assert "max=72" in out                       # peak visible for delta
    assert '"raw"' not in out                     # no fat raw-row dump


def test_format_data_does_not_truncate_relevant_series():
    # 3 variables x 30 rows = 90 rows. The old 40-ROW global cap showed only the
    # first ~1.3 variables; grouping keeps all three visible.
    rows = [{"variable": v, "scenario": "severely_adverse", "value": i}
            for v in ("treasury_10y", "vix", "bbb_corporate_yield")
            for i in range(30)]
    out = format_data([TimeseriesHit(source="S", publisher="P", rows=rows)])
    for v in ("treasury_10y", "vix", "bbb_corporate_yield"):
        assert f"{v} [severely_adverse]:" in out


def test_format_data_empty():
    assert format_data([]) == "(no numeric series)"
