"""Self-validation harness — differential testing of the answer path against the
stored numeric answer key (validation plan, Loops 1 + 5).

The ``TimeseriesStore`` is populated by deterministic parsers at ingest time; the
answer path runs embed → retrieve → rerank → generate. Those are **independent
code paths over the same source documents**, so a disagreement between them is a
real defect in one of the two — which makes the store a machine-checkable answer
key that needs no human labelling and no LLM judge.

Two families, both deterministic:

**Loop 1 — grounded numeric cases.** Every stored ``(variable, scenario, period,
value)`` row templates a natural-language question whose answer we already know.
Each case is scored on two *independent* signals: whether hydration surfaced the
right row (structural) and whether the answer prose states the right number
(regex over the text). The pair localises a failure — hydrated-but-unstated is a
generation bug, neither is a retrieval or store bug.

**Loop 5 — negative controls.** Probes whose only correct response is a refusal:
variables and scenarios that do not exist in the corpus, and false-premise
questions that presuppose one. Outcomes are classified four ways rather than
pass/fail, because "the severely adverse scenario (there is no *extremely*
adverse one) shows 10.0%" is a *correct* answer that names a number — collapsing
it into a failure would punish the best behaviour. Only ``fabricated`` counts
against the system.

Both runners take an injected ``answer_fn`` so they run under test with a fake
and in production against the real pipeline.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Optional

from utils.logging import get_logger

log = get_logger(__name__)

# answer_fn(question) -> an Answer-like object exposing .summary / .details /
# .refused / .timeseries (utils.schema.Answer satisfies this).
AnswerFn = Callable[[str], Any]

# Scenario labels that are structural rather than forward-looking: a question
# about them reads oddly ("in the historic scenario…") so they template without
# the scenario clause. ``baseline`` is deliberately NOT here — it is a real,
# named scenario, and dropping it would make the question ambiguous against
# ``severely_adverse`` for the same period, producing false failures.
_PLAIN_SCENARIOS = {"historic", None, ""}


# ── case types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NumericCase:
    """One known-answer question generated from a stored observation."""
    case_id: str
    question: str
    variable: str
    period: str
    expected: float
    source: str
    scenario: str | None = None
    tol: float = 0.0


@dataclass(frozen=True)
class ProbeCase:
    """One negative control whose only correct response is a refusal."""
    case_id: str
    question: str
    kind: str            # absent_scenario | absent_variable | false_premise
    detail: str = ""


# ── phrasing ─────────────────────────────────────────────────────────────────


def _phrase_variable(variable: str) -> str:
    """``treasury_10y`` -> ``the treasury 10y``. Deliberately literal: we are
    testing retrieval of a known series, not paraphrase robustness."""
    return "the " + str(variable).replace("_", " ").strip()


def _phrase_scenario(scenario: str | None) -> str:
    return str(scenario or "").replace("_", " ").strip()


def _phrase_source(source: str) -> str:
    """``DFAST_2026`` -> ``DFAST 2026``."""
    return str(source or "").replace("_", " ").strip()


def build_question(variable: str, period: str, source: str,
                   scenario: str | None = None) -> str:
    var, src = _phrase_variable(variable), _phrase_source(source)
    if scenario in _PLAIN_SCENARIOS:
        return f"According to {src}, what is the value of {var} in {period}?"
    return (f"In the {src} {_phrase_scenario(scenario)} scenario, "
            f"what is the value of {var} in {period}?")


# ── numeric tolerance + text extraction ──────────────────────────────────────


def tolerance_for(expected: float, *, rel_tol: float = 0.01,
                  abs_tol: float = 0.05) -> float:
    """Band for "the answer states this number". Relative, with an absolute floor
    so small values (a 4.2% unemployment rate) don't get an unusably tight band.
    Generous by intent — we are catching wrong numbers, not prose rounding."""
    return max(abs(float(expected)) * rel_tol, abs_tol)


_NUMBER_RE = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


def extract_numbers(text: str) -> list[float]:
    """Every number in a block of prose, comma-grouping removed. Used to ask
    "did the answer state this value anywhere", which is deliberately weaker
    than "did it state it in the right place" — a false PASS here is possible,
    a false FAIL is not, so the metric is a floor on correctness."""
    out: list[float] = []
    for m in _NUMBER_RE.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:                                   # pragma: no cover
            continue
    return out


def _answer_text(answer: Any) -> str:
    """Flatten summary + detail bodies into one searchable string."""
    parts = [getattr(answer, "summary", "") or ""]
    for d in getattr(answer, "details", None) or []:
        parts.append(getattr(d, "text", "") or "")
    return "\n".join(parts)


def _near(a: float, b: float, tol: float) -> bool:
    return abs(float(a) - float(b)) <= tol


# ── Loop 1: generate + score numeric cases ───────────────────────────────────


def _evenly_spaced(items: list[Any], n: int) -> list[Any]:
    """``n`` items spread across the list, endpoints included. Deterministic (no
    RNG) so a self-check run is reproducible and diffable across builds."""
    if n <= 0 or not items:
        return []
    if n >= len(items):
        return list(items)
    if n == 1:
        return [items[0]]
    step = (len(items) - 1) / (n - 1)
    return [items[int(round(i * step))] for i in range(n)]


def generate_numeric_cases(store: Any, *, per_group: int = 2,
                           max_cases: int = 200,
                           sources: Iterable[str] | None = None,
                           rel_tol: float = 0.01,
                           abs_tol: float = 0.05) -> list[NumericCase]:
    """Template known-answer questions from the store.

    Samples ``per_group`` observations from each (source, variable, scenario)
    series, evenly spaced across the series' period order, capped at
    ``max_cases``. ``sources`` restricts to named sources (else all)."""
    wanted = {str(s) for s in sources} if sources else None
    cases: list[NumericCase] = []
    for grp in store.groups():
        if wanted is not None and str(grp["source"]) not in wanted:
            continue
        rows = [r for r in store.query(source=grp["source"],
                                       variable=grp["variable"],
                                       scenario=grp["scenario"],
                                       limit=5000)
                if r.get("value") is not None and r.get("period")]
        if not rows:
            continue
        rows.sort(key=lambda r: str(r.get("period")))
        for row in _evenly_spaced(rows, per_group):
            expected = float(row["value"])
            period = str(row["period"])
            cases.append(NumericCase(
                case_id=f"{grp['source']}|{grp['variable']}|"
                        f"{grp['scenario'] or '-'}|{period}",
                question=build_question(grp["variable"], period,
                                        grp["source"], grp["scenario"]),
                variable=str(grp["variable"]), period=period,
                expected=expected, source=str(grp["source"]),
                scenario=grp["scenario"],
                tol=tolerance_for(expected, rel_tol=rel_tol, abs_tol=abs_tol),
            ))
            if len(cases) >= max_cases:
                return cases
    return cases


def _hydrated_match(answer: Any, case: NumericCase) -> float | None:
    """The value hydration surfaced for this case's (variable, period[, scenario]),
    or None when the right row never came back."""
    for hit in getattr(answer, "timeseries", None) or []:
        for row in getattr(hit, "rows", None) or []:
            if str(row.get("variable")) != case.variable:
                continue
            if str(row.get("period")) != case.period:
                continue
            if case.scenario and str(row.get("scenario")) != str(case.scenario):
                continue
            val = row.get("value")
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):              # pragma: no cover
                    continue
    return None


def score_numeric_case(case: NumericCase, answer: Any) -> dict[str, Any]:
    """Score one case on the two independent signals (hydration / statement)."""
    refused = bool(getattr(answer, "refused", False))
    text = _answer_text(answer)
    hydrated_value = _hydrated_match(answer, case)
    hydrated = hydrated_value is not None and _near(hydrated_value,
                                                    case.expected, case.tol)
    numbers = extract_numbers(text)
    stated = any(_near(n, case.expected, case.tol) for n in numbers)

    if refused:
        status = "refused"
    elif hydrated and stated:
        status = "ok"
    elif hydrated:
        status = "statement_miss"        # right row retrieved, prose got it wrong
    elif stated:
        status = "hydration_miss"        # prose right, structured path missed it
    else:
        status = "both_miss"

    return {
        "case_id": case.case_id, "question": case.question,
        "variable": case.variable, "scenario": case.scenario,
        "period": case.period, "source": case.source,
        "expected": case.expected, "tol": case.tol,
        "hydrated": hydrated, "hydrated_value": hydrated_value,
        "stated": stated, "refused": refused, "status": status,
        "numbers_seen": numbers[:12],
    }


def summarise_numeric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n_cases": 0}

    def rate(pred: Callable[[dict], bool]) -> float:
        return sum(1 for r in rows if pred(r)) / n

    return {
        "n_cases": n,
        # the headline: structured path AND prose both correct
        "numeric_agreement": rate(lambda r: r["status"] == "ok"),
        "hydration_rate": rate(lambda r: r["hydrated"]),
        "statement_rate": rate(lambda r: r["stated"]),
        "refusal_rate": rate(lambda r: r["refused"]),
        "both_miss_rate": rate(lambda r: r["status"] == "both_miss"),
    }


def run_numeric_cases(cases: list[NumericCase], answer_fn: AnswerFn, *,
                      should_cancel: Callable[[], None] | None = None
                      ) -> dict[str, Any]:
    rows = []
    for case in cases:
        if should_cancel is not None:
            should_cancel()
        rows.append(score_numeric_case(case, answer_fn(case.question)))
    return {"summary": summarise_numeric(rows), "rows": rows}


# ── Loop 5: negative controls ────────────────────────────────────────────────

# Scenario labels that read as plausible official stress scenarios but are not
# in the DFAST/EBA vocabulary. A system that answers a question about one has
# invented a scenario.
FAKE_SCENARIOS = ("extremely adverse", "catastrophic", "moderately adverse",
                  "severely optimistic")

# Series that plausibly belong in a macro corpus but are NOT ingested here, so
# any specific number attached to one is fabricated.
FAKE_VARIABLES = ("the copper-to-gold ratio", "the Baltic Dry Index",
                  "the bitcoin spot price", "the Case-Shiller condominium index")


def generate_probe_cases(store: Any, *, per_kind: int = 4) -> list[ProbeCase]:
    """Build negative controls anchored to what the store actually contains, so
    the "absent" half is absent *by construction* rather than by assumption."""
    groups = store.groups()
    real_scenarios = {str(s) for s in store.scenarios()}
    # A real (source, variable) pair to hang false premises on.
    anchors = [(str(g["source"]), str(g["variable"])) for g in groups
               if g.get("variable")]
    cases: list[ProbeCase] = []

    for i, fake in enumerate(FAKE_SCENARIOS[:per_kind]):
        if fake.replace(" ", "_") in real_scenarios or not anchors:
            continue                                   # guard: it must be absent
        source, variable = anchors[i % len(anchors)]
        cases.append(ProbeCase(
            case_id=f"absent_scenario|{fake}|{variable}",
            question=(f"In the {_phrase_source(source)} {fake} scenario, what is "
                      f"the peak value of {_phrase_variable(variable)}?"),
            kind="absent_scenario",
            detail=f"'{fake}' is not a scenario in the corpus"))

    for i, fake_var in enumerate(FAKE_VARIABLES[:per_kind]):
        source = anchors[i % len(anchors)][0] if anchors else "the corpus"
        cases.append(ProbeCase(
            case_id=f"absent_variable|{fake_var}",
            question=(f"According to {_phrase_source(source)}, what is the "
                      f"projected value of {fake_var} next year?"),
            kind="absent_variable",
            detail=f"'{fake_var}' is not an ingested series"))

    for i, fake in enumerate(FAKE_SCENARIOS[:per_kind]):
        if not anchors:
            break
        source, variable = anchors[(i + 1) % len(anchors)]
        cases.append(ProbeCase(
            case_id=f"false_premise|{fake}|{variable}",
            question=(f"By how many basis points does {_phrase_variable(variable)} "
                      f"fall in the {fake} scenario that {_phrase_source(source)} "
                      f"published this year?"),
            kind="false_premise",
            detail=f"presupposes a '{fake}' scenario that was never published"))

    return cases


def score_probe_case(case: ProbeCase, answer: Any,
                     real_scenarios: Iterable[str] = ()) -> dict[str, Any]:
    """Classify a probe response.

    ``refused``   — the relevance floor or the generator declined. Ideal.
    ``corrected`` — it named a *real* scenario instead, i.e. it pushed back on
                    the false premise. Also correct, and must not be scored as a
                    fabrication just because the correction carries a number.
    ``fabricated``— it asserted a number without correcting. The failure we care
                    about.
    ``evasive``   — no number, no refusal, no correction. Not wrong, not useful.
    """
    refused = bool(getattr(answer, "refused", False))
    text = _answer_text(answer)
    numbers = extract_numbers(text)
    lowered = text.lower()
    named_real = sorted({s for s in real_scenarios
                         if s and _phrase_scenario(s).lower() in lowered})

    if refused:
        outcome = "refused"
    elif named_real:
        outcome = "corrected"
    elif numbers:
        outcome = "fabricated"
    else:
        outcome = "evasive"

    return {
        "case_id": case.case_id, "question": case.question, "kind": case.kind,
        "detail": case.detail, "outcome": outcome, "refused": refused,
        "named_real_scenarios": named_real, "numbers_seen": numbers[:12],
        "answer_excerpt": text[:280],
    }


def summarise_probes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"n_probes": 0}

    def rate(outcome: str) -> float:
        return sum(1 for r in rows if r["outcome"] == outcome) / n

    fabricated = rate("fabricated")
    return {
        "n_probes": n,
        "fabrication_rate": fabricated,          # lower is better
        "abstention_rate": rate("refused") + rate("corrected"),
        "refused_rate": rate("refused"),
        "corrected_rate": rate("corrected"),
        "evasive_rate": rate("evasive"),
    }


def run_probe_cases(cases: list[ProbeCase], answer_fn: AnswerFn, *,
                    real_scenarios: Iterable[str] = (),
                    should_cancel: Callable[[], None] | None = None
                    ) -> dict[str, Any]:
    real = list(real_scenarios)
    rows = []
    for case in cases:
        if should_cancel is not None:
            should_cancel()
        rows.append(score_probe_case(case, answer_fn(case.question), real))
    return {"summary": summarise_probes(rows), "rows": rows}


# ── combined runner ──────────────────────────────────────────────────────────

# Metrics where a LOWER value is better (the drift gate must not read a falling
# fabrication rate as a regression).
LOWER_BETTER = ("fabrication_rate", "both_miss_rate", "evasive_rate")


@dataclass
class SelfCheckReport:
    summary: dict[str, Any] = field(default_factory=dict)
    numeric: dict[str, Any] = field(default_factory=dict)
    probes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary, "numeric": self.numeric,
                "probes": self.probes}


def run_selfcheck(store: Any, answer_fn: AnswerFn, *, per_group: int = 2,
                  max_cases: int = 200, per_kind: int = 4,
                  sources: Iterable[str] | None = None,
                  should_cancel: Callable[[], None] | None = None,
                  on_progress: Callable[[str], None] | None = None
                  ) -> SelfCheckReport:
    """Run Loop 1 + Loop 5 and merge their summaries into one flat metric block
    (the shape the drift gate and the Quality screen already consume)."""
    def note(msg: str) -> None:
        if on_progress is not None:
            on_progress(msg)
        log.info("selfcheck: %s", msg)

    cases = generate_numeric_cases(store, per_group=per_group,
                                   max_cases=max_cases, sources=sources)
    note(f"generated {len(cases)} numeric cases from the time-series store")
    numeric = run_numeric_cases(cases, answer_fn, should_cancel=should_cancel)

    probes = generate_probe_cases(store, per_kind=per_kind)
    note(f"generated {len(probes)} negative-control probes")
    probe_report = run_probe_cases(probes, answer_fn,
                                   real_scenarios=store.scenarios(),
                                   should_cancel=should_cancel)

    summary = {**numeric["summary"], **probe_report["summary"]}
    note("summary: " + ", ".join(
        f"{k}={v:.3f}" for k, v in summary.items() if isinstance(v, float)))
    return SelfCheckReport(summary=summary, numeric=numeric, probes=probe_report)
