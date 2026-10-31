"""C1: the plan schema accepts null for paraphrase / subqueries / hyde and keeps every other
type rule."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llm.base import schema_errors

OPTIONAL = ("paraphrase", "subqueries", "hyde")
# spec C1: wrong-typed non-null values for each optional field
BAD = {"paraphrase": (3, ["x"], {"a": 1}, True),
       "subqueries": ("x", 3, {"a": 1}, True),
       "hyde": ("x", 3, {"a": 1}, True)}


@pytest.mark.criterion("C1")
def test_pilot_reply_with_null_paraphrase_validates(psn):
    # oracle: spec Notes: the pilot's failing reply shape was a string route with
    # `"paraphrase": null`; the Requirement says that reply must now validate (no errors).
    reply = {"route": "DIRECT", "paraphrase": None, "subqueries": [], "hyde": []}
    assert schema_errors(reply, psn.plan_schema()) == []


@pytest.mark.criterion("C1")
def test_each_and_all_optional_fields_may_be_null(psn):
    # oracle: spec C1, hand-enumerated: route a string and each optional field absent, null
    # or of its type -> no schema errors.
    schema = psn.plan_schema()
    replies = [
        {"route": "DECOMPOSE", "paraphrase": None, "subqueries": ["a", "b"], "hyde": None},
        {"route": "HYDE", "paraphrase": "p", "subqueries": None, "hyde": ["doc"]},
        {"route": "PARAPHRASE", "paraphrase": "p", "subqueries": [], "hyde": None},
        {"route": "DIRECT", "paraphrase": None, "subqueries": None, "hyde": None},
        {"route": "DIRECT", "paraphrase": None},
        {"route": "DIRECT", "hyde": None, "extra": 1},     # additional properties allowed
    ]
    for reply in replies:
        assert schema_errors(reply, schema) == [], reply


@pytest.mark.criterion("C1")
def test_type_rules_stay_while_null_is_allowed(psn):
    # oracle: spec C1: null is accepted for each optional field, but a non-null non-string
    # paraphrase, a non-null non-array subqueries / hyde, and a null or non-string route are
    # still rejected.
    schema = psn.plan_schema()
    for name in OPTIONAL:
        assert schema_errors({"route": "DIRECT", name: None}, schema) == [], name
        for bad in BAD[name]:
            assert schema_errors({"route": "DIRECT", name: bad}, schema), (name, bad)
    for route in (None, 3, ["DIRECT"], {"r": "DIRECT"}):
        reply = {"route": route, "paraphrase": None, "subqueries": None, "hyde": None}
        assert schema_errors(reply, schema), route


_STR = st.text(max_size=12)
_VALID = {"paraphrase": _STR, "subqueries": st.lists(_STR, max_size=4),
          "hyde": st.lists(_STR, max_size=3)}


@st.composite
def _valid_replies(draw):
    """A string route; each optional field absent, null or of its type; at least one null."""
    reply = {"route": draw(st.sampled_from(["DIRECT", "PARAPHRASE", "DECOMPOSE", "HYDE"]))}
    forced = draw(st.sampled_from(OPTIONAL))
    for name in OPTIONAL:
        kind = "null" if name == forced else draw(st.sampled_from(["absent", "null", "value"]))
        if kind == "null":
            reply[name] = None
        elif kind == "value":
            reply[name] = draw(_VALID[name])
    return reply


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None)
@given(reply=_valid_replies())
def test_property_null_or_typed_optional_fields_validate(psn, reply):
    # oracle: spec C1: every reply with a string route and optional fields absent, null or
    # of their type has no schema errors.
    assert schema_errors(reply, psn.plan_schema()) == []


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@settings(max_examples=60, deadline=None)
@given(reply=_valid_replies(), field=st.sampled_from(OPTIONAL), data=st.data())
def test_metamorphic_bad_value_rejected_null_accepted(psn, reply, field, data):
    # oracle: spec C1, metamorphic: from a valid reply, setting one optional field to a
    # wrong-typed value must produce errors; setting that same field to null must not.
    schema = psn.plan_schema()
    bad = dict(reply)
    bad[field] = data.draw(st.sampled_from(BAD[field]))
    assert schema_errors(bad, schema), bad
    nulled = dict(reply)
    nulled[field] = None
    assert schema_errors(nulled, schema) == [], nulled
