"""C2: a plan reply with null optional fields validates first time inside LLMAnalyzer: one
call, outcome ok, the analysis unchanged."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llm.schema_health import capture_schema
from llm.understanding import LLMAnalyzer
from utils.config import RetrievalSettings

KEYS = ("ok", "repaired", "failed", "refused", "truncated")
ONE_OK = {"ok": 1, "repaired": 0, "failed": 0, "refused": 0, "truncated": 0}


def _analyze(psn, reply: dict):
    """Run LLMAnalyzer over the double inside capture_schema; (analysis, double, counts)."""
    llm = psn.PlanReplier(reply)
    with capture_schema() as counts:
        analyzer = LLMAnalyzer(llm, RetrievalSettings(), plan_model=psn.PLAN_MODEL)
        analysis = analyzer.analyze(psn.QUESTION)
    return analysis, llm, {k: getattr(counts, k) for k in KEYS}


@pytest.mark.criterion("C2")
def test_decompose_reply_with_nulls_is_one_ok_call(psn):
    # oracle: spec C2: this exact reply -> complete called once with purpose `plan` (no
    # `plan:repair`), exactly one `ok` outcome and nothing else, route DECOMPOSE, no
    # paraphrase, subqueries ["a", "b"], no HyDE documents.
    reply = {"route": "DECOMPOSE", "paraphrase": None, "subqueries": ["a", "b"],
             "hyde": None}
    analysis, llm, counts = _analyze(psn, reply)
    assert llm.purposes == ["plan"]
    assert counts == ONE_OK
    assert analysis.route == "DECOMPOSE"
    assert analysis.paraphrase is None
    assert analysis.subqueries == ["a", "b"]
    assert analysis.hyde_docs == []


@pytest.mark.criterion("C2")
def test_pilot_reply_null_paraphrase_is_one_ok_call(psn):
    # oracle: spec Notes: the pilot replies failed only on `paraphrase: null`; such a reply
    # must now take one `plan` call, record `ok`, and give route DIRECT with nothing else.
    reply = {"route": "DIRECT", "paraphrase": None, "subqueries": [], "hyde": []}
    analysis, llm, counts = _analyze(psn, reply)
    assert llm.purposes == ["plan"]
    assert counts == ONE_OK
    assert (analysis.route, analysis.paraphrase) == ("DIRECT", None)
    assert analysis.subqueries == [] and analysis.hyde_docs == []


_ROUTE = st.sampled_from(["DIRECT", "PARAPHRASE", "DECOMPOSE", "HYDE"])
_WORD = st.text(alphabet="abcdefghij ", min_size=1, max_size=10).filter(str.strip)
_VALUES = {"paraphrase": _WORD, "subqueries": st.lists(_WORD, min_size=1, max_size=5),
           "hyde": st.lists(_WORD, min_size=1, max_size=2)}


@st.composite
def _replies(draw):
    """A string route; each of the three fields null or a valid value; at least one null."""
    reply = {"route": draw(_ROUTE)}
    forced = draw(st.sampled_from(tuple(_VALUES)))
    for name, strat in _VALUES.items():
        is_null = name == forced or draw(st.booleans())
        reply[name] = None if is_null else draw(strat)
    return reply


@pytest.mark.criterion("C2")
@settings(max_examples=40, deadline=None)
@given(reply=_replies())
def test_property_any_null_combination_is_one_ok_call(psn, reply):
    # oracle: spec C2: for any combination of null / valid values of the three fields the
    # call count stays one and the outcome `ok`; the analysis takes the route from `route`,
    # the paraphrase only from a non-null value, subqueries / HyDE only from non-empty arrays
    # (the drawn arrays hold non-blank strings within the analyzer's 5 / 2 caps).
    analysis, llm, counts = _analyze(psn, reply)
    assert llm.purposes == ["plan"]
    assert counts == ONE_OK
    assert analysis.route == reply["route"]
    assert analysis.paraphrase == reply["paraphrase"]
    assert analysis.subqueries == (reply["subqueries"] or [])
    assert analysis.hyde_docs == (reply["hyde"] or [])
