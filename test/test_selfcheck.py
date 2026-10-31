"""Self-validation harness (eval/selfcheck.py): question generation from the
time-series store, the two independent numeric signals, and the negative-control
outcome classification. All fakes — no torch, no network, no LLM."""

from __future__ import annotations

from eval import selfcheck
from eval.selfcheck import (
    NumericCase,
    ProbeCase,
    build_question,
    extract_numbers,
    generate_numeric_cases,
    generate_probe_cases,
    run_selfcheck,
    score_numeric_case,
    score_probe_case,
    tolerance_for,
)
from utils.schema import Answer, AnswerDetail, TimeseriesHit
from vectorstores.timeseries_store import TimeseriesStore

# ── fixtures ─────────────────────────────────────────────────────────────────

def _store(tmp_path) -> TimeseriesStore:
    """A miniature DFAST-shaped store: two variables x two scenarios."""
    rows = []
    for scenario, base in (("baseline", 3.0), ("severely_adverse", 9.0)):
        for i, period in enumerate(("2026-Q1", "2026-Q2", "2026-Q3")):
            rows.append({"doc_id": "d1", "source": "DFAST_2026", "tier": 1,
                         "publisher": "frb", "variable": "unemployment_rate",
                         "scenario": scenario, "period": period,
                         "value": base + i})
            rows.append({"doc_id": "d1", "source": "DFAST_2026", "tier": 1,
                         "publisher": "frb", "variable": "treasury_10y",
                         "scenario": scenario, "period": period,
                         "value": 4.0 - i * 0.5})
    st = TimeseriesStore(tmp_path / "ts.sqlite")
    st.build(rows)
    return st


def _answer(summary: str = "", *, refused: bool = False,
            rows: list[dict] | None = None, detail: str = "") -> Answer:
    hits = [TimeseriesHit("DFAST_2026", "frb", rows or [])] if rows else []
    details = [AnswerDetail(detail)] if detail else []
    return Answer(summary=summary, details=details, refused=refused,
                  timeseries=hits)


# ── store enumeration helpers ────────────────────────────────────────────────

def test_store_groups_and_scenarios(tmp_path):
    st = _store(tmp_path)
    groups = st.groups()
    assert len(groups) == 4                      # 2 variables x 2 scenarios
    assert all(g["n"] == 3 for g in groups)
    assert st.scenarios() == ["baseline", "severely_adverse"]


def test_store_groups_empty_when_unbuilt(tmp_path):
    st = TimeseriesStore(tmp_path / "missing.sqlite")
    assert st.groups() == [] and st.scenarios() == []


# ── question generation ──────────────────────────────────────────────────────

def test_build_question_includes_scenario():
    q = build_question("treasury_10y", "2026-Q2", "DFAST_2026", "severely_adverse")
    assert "severely adverse scenario" in q
    assert "the treasury 10y" in q and "2026-Q2" in q


def test_build_question_omits_only_structural_scenarios():
    # 'historic' reads oddly as "in the historic scenario"; it is also the only
    # label with no sibling to be confused with at the same period
    for scenario in ("historic", None, ""):
        q = build_question("treasury_10y", "2026-Q2", "DFAST_2026", scenario)
        assert "scenario" not in q


def test_build_question_keeps_baseline_explicit():
    # baseline vs severely_adverse share periods — dropping the label would make
    # the question ambiguous and fail a correct answer
    q = build_question("treasury_10y", "2026-Q2", "DFAST_2026", "baseline")
    assert "baseline scenario" in q


def test_generate_numeric_cases_samples_each_group(tmp_path):
    cases = generate_numeric_cases(_store(tmp_path), per_group=2)
    assert len(cases) == 8                       # 4 groups x 2 samples
    # evenly spaced picks the endpoints of each 3-row series
    periods = {c.period for c in cases}
    assert periods == {"2026-Q1", "2026-Q3"}
    assert all(c.tol > 0 for c in cases)


def test_generate_numeric_cases_respects_max_and_source_filter(tmp_path):
    st = _store(tmp_path)
    assert len(generate_numeric_cases(st, per_group=2, max_cases=3)) == 3
    assert generate_numeric_cases(st, sources=["NOT_A_SOURCE"]) == []


def test_generate_numeric_cases_empty_store(tmp_path):
    assert generate_numeric_cases(TimeseriesStore(tmp_path / "x.sqlite")) == []


# ── tolerance + number extraction ────────────────────────────────────────────

def test_tolerance_has_absolute_floor_for_small_values():
    assert tolerance_for(4.2) == 0.05            # relative would be 0.042
    assert tolerance_for(10000.0) == 100.0       # relative dominates


def test_extract_numbers_handles_commas_negatives_decimals():
    got = extract_numbers("fell to -2.5% from 1,234.75 and 40 bp")
    assert got == [-2.5, 1234.75, 40.0]


def test_extract_numbers_on_empty_text():
    assert extract_numbers("") == [] and extract_numbers(None) == []


# ── Loop 1 scoring: the two independent signals ──────────────────────────────

_CASE = NumericCase(case_id="c1", question="q?", variable="unemployment_rate",
                    period="2026-Q2", expected=10.0, source="DFAST_2026",
                    scenario="severely_adverse", tol=0.05)

_GOOD_ROW = {"variable": "unemployment_rate", "period": "2026-Q2",
             "scenario": "severely_adverse", "value": 10.0}


def test_numeric_ok_when_hydrated_and_stated():
    r = score_numeric_case(_CASE, _answer("It reaches 10.0 percent.", rows=[_GOOD_ROW]))
    assert r["status"] == "ok" and r["hydrated"] and r["stated"]


def test_numeric_statement_miss_localises_generation_bug():
    # right row hydrated, prose quotes a different number
    r = score_numeric_case(_CASE, _answer("It reaches 7.5 percent.", rows=[_GOOD_ROW]))
    assert r["status"] == "statement_miss"
    assert r["hydrated"] and not r["stated"]


def test_numeric_hydration_miss_localises_structured_path():
    r = score_numeric_case(_CASE, _answer("It reaches 10.0 percent."))
    assert r["status"] == "hydration_miss"
    assert r["stated"] and not r["hydrated"]


def test_numeric_both_miss():
    r = score_numeric_case(_CASE, _answer("Unemployment rises materially."))
    assert r["status"] == "both_miss"


def test_numeric_refusal_is_its_own_status():
    r = score_numeric_case(_CASE, _answer("", refused=True))
    assert r["status"] == "refused" and r["refused"]


def test_numeric_statement_found_in_detail_body():
    # the number may live in a detail rather than the summary
    r = score_numeric_case(_CASE, _answer("See below.", detail="peaks at 10.0"))
    assert r["stated"]


def test_hydration_requires_matching_scenario():
    wrong = dict(_GOOD_ROW, scenario="baseline")
    r = score_numeric_case(_CASE, _answer("10.0", rows=[wrong]))
    assert not r["hydrated"]                     # right value, wrong scenario


def test_hydration_requires_matching_period():
    wrong = dict(_GOOD_ROW, period="2026-Q3")
    r = score_numeric_case(_CASE, _answer("10.0", rows=[wrong]))
    assert not r["hydrated"]


# ── Loop 5: negative controls ────────────────────────────────────────────────

def test_generate_probe_cases_covers_all_three_kinds(tmp_path):
    probes = generate_probe_cases(_store(tmp_path), per_kind=2)
    kinds = {p.kind for p in probes}
    assert kinds == {"absent_scenario", "absent_variable", "false_premise"}


def test_generate_probe_cases_never_uses_a_real_scenario(tmp_path):
    st = _store(tmp_path)
    real = {s.replace("_", " ") for s in st.scenarios()}
    for p in generate_probe_cases(st):
        if p.kind == "absent_scenario":
            assert not any(r in p.question for r in real)


_PROBE = ProbeCase(case_id="p1", question="q?", kind="absent_scenario")


def test_probe_refusal_is_the_ideal_outcome():
    r = score_probe_case(_PROBE, _answer("", refused=True))
    assert r["outcome"] == "refused"


def test_probe_fabrication_is_a_number_without_a_correction():
    r = score_probe_case(_PROBE, _answer("It falls by 120 basis points."),
                         real_scenarios=["baseline", "severely_adverse"])
    assert r["outcome"] == "fabricated"


def test_probe_correction_is_not_scored_as_fabrication():
    # naming a REAL scenario means it pushed back on the false premise, even
    # though the answer carries a number
    r = score_probe_case(
        _PROBE,
        _answer("There is no such scenario; the severely adverse one shows 10.0."),
        real_scenarios=["baseline", "severely_adverse"])
    assert r["outcome"] == "corrected"
    assert r["named_real_scenarios"] == ["severely_adverse"]


def test_probe_evasive_when_no_number_no_refusal():
    r = score_probe_case(_PROBE, _answer("That is difficult to say."))
    assert r["outcome"] == "evasive"


# ── combined runner ──────────────────────────────────────────────────────────

def test_run_selfcheck_merges_summaries_and_counts(tmp_path):
    st = _store(tmp_path)
    seen: list[str] = []

    def answer_fn(question: str) -> Answer:
        seen.append(question)
        return _answer("", refused=True)         # refuse everything

    report = run_selfcheck(st, answer_fn, per_group=1, per_kind=1)
    s = report.summary
    assert s["n_cases"] == 4 and s["n_probes"] == 3
    assert s["refusal_rate"] == 1.0              # Loop 1: refused every case
    assert s["fabrication_rate"] == 0.0          # Loop 5: fabricated nothing
    assert s["abstention_rate"] == 1.0
    assert len(seen) == 7
    assert set(report.to_dict()) == {"summary", "numeric", "probes"}


def test_run_selfcheck_perfect_system_scores_one(tmp_path):
    st = _store(tmp_path)
    lookup = {c.question: c for c in generate_numeric_cases(st, per_group=1)}

    def answer_fn(question: str) -> Answer:
        case = lookup.get(question)
        if case is None:                          # a probe -> refuse
            return _answer("", refused=True)
        row = {"variable": case.variable, "period": case.period,
               "scenario": case.scenario, "value": case.expected}
        return _answer(f"The value is {case.expected}.", rows=[row])

    s = run_selfcheck(st, answer_fn, per_group=1, per_kind=1).summary
    assert s["numeric_agreement"] == 1.0
    assert s["hydration_rate"] == 1.0 and s["statement_rate"] == 1.0
    assert s["fabrication_rate"] == 0.0


def test_run_selfcheck_cancellation_propagates(tmp_path):
    class Cancelled(RuntimeError):
        pass

    def cancel():
        raise Cancelled()

    try:
        run_selfcheck(_store(tmp_path), lambda q: _answer(""),
                      should_cancel=cancel)
    except Cancelled:
        return
    raise AssertionError("should_cancel was never honoured")


def test_lower_better_metrics_are_declared():
    # the drift gate must not read a falling fabrication rate as a regression
    assert "fabrication_rate" in selfcheck.LOWER_BETTER
