"""C6: complete_json fails soft on refusals, keeps the repair on truncation, and counts both."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llm.base import LLMClient

SCHEMA = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}
REFUSE = object()        # script marker: the provider refuses this call
KEYS = ("ok", "repaired", "failed", "refused", "truncated")


class Scripted(LLMClient):
    """Provider-boundary double: each complete() takes the next scripted reply, a
    ``(text, finish_reason)`` pair, sets ``last_finish_reason`` and returns the text; the
    REFUSE marker sets ``last_finish_reason = "refusal"`` and raises LLMRefusalError."""

    def __init__(self, script):
        self.model = "gpt-5.4-mini"
        self.script = list(script)
        self.calls: list[str | None] = []
        self.last_finish_reason = None

    def complete(self, *, system, user, temperature=None, model=None, purpose=None):
        from llm.base import LLMRefusalError

        self.calls.append(purpose)
        item = self.script.pop(0)
        if item is REFUSE:
            self.last_finish_reason = "refusal"
            raise LLMRefusalError(model or self.model, "cyber")
        text, finish = item
        self.last_finish_reason = finish
        return text


def _run(script):
    """complete_json with SCHEMA inside capture_schema; returns (result, double, counts)."""
    from llm.schema_health import capture_schema

    llm = Scripted(script)
    with capture_schema() as counts:
        out = llm.complete_json(system="s", user="u", schema=SCHEMA, purpose="t")
    return out, llm, counts


def _counts(counts) -> dict:
    """The five outcome counters as the test sees them (missing -> None)."""
    return {k: getattr(counts, k, None) for k in KEYS}


def _zero(**kw) -> dict:
    return {**dict.fromkeys(KEYS, 0), **kw}


@pytest.mark.criterion("C6")
def test_refused_first_reply_returns_empty_without_repair():
    # oracle: spec Requirement "Refusals fail soft": no repair call follows a refused first
    # reply; the outcome is `refused` and the result is {}.
    out, llm, counts = _run([REFUSE, ('{"a": 1}', "stop")])
    assert out == {}
    assert len(llm.calls) == 1
    assert _counts(counts) == _zero(refused=1)


@pytest.mark.criterion("C6")
def test_refused_repair_returns_the_first_parse():
    # oracle: spec Requirement: a refused repair call records `refused` and returns the first
    # reply's best-effort parse ({"b": 1} fails the schema, so a repair is attempted).
    out, llm, counts = _run([('{"b": 1}', "stop"), REFUSE])
    assert out == {"b": 1}
    assert len(llm.calls) == 2
    assert _counts(counts) == _zero(refused=1)


@pytest.mark.criterion("C6")
def test_valid_length_reply_is_ok_and_truncated():
    # oracle: spec Requirement "Truncation is visible": a valid reply cut off at the cap
    # records `ok` plus `truncated`; no repair (one call).
    out, llm, counts = _run([('{"a": 1}', "length")])
    assert out == {"a": 1}
    assert len(llm.calls) == 1
    assert _counts(counts) == _zero(ok=1, truncated=1)


@pytest.mark.criterion("C6")
def test_invalid_length_reply_is_still_repaired():
    # oracle: spec Requirement: the repair call still happens when a cut-off reply fails its
    # schema; repaired -> `repaired` + `truncated`; still invalid -> `failed` + `truncated`
    # (truncated once per complete_json call even when both replies were cut off).
    out, llm, counts = _run([('{"a": ', "length"), ('{"a": 2}', "stop")])
    assert out == {"a": 2}
    assert len(llm.calls) == 2
    assert _counts(counts) == _zero(repaired=1, truncated=1)
    out, llm, counts = _run([('{"b": 3}', "length"), ('{"a": "x', "length")])
    assert out == {"b": 3}
    assert len(llm.calls) == 2
    assert _counts(counts) == _zero(failed=1, truncated=1)


@pytest.mark.criterion("C6")
def test_counts_dict_and_admin_totals():
    # oracle: spec Requirement "Schema-health counts": as_dict adds refused and truncated;
    # total = ok + repaired + failed + refused; repair_rate = (repaired + failed) / total.
    # Hand calculation for 2 ok (one truncated), 1 repaired, 1 failed, 1 refused:
    #   total = 2 + 1 + 1 + 1 = 5; repair_rate = (1 + 1)/5 = 0.4; truncated = 1.
    from llm.schema_health import capture_schema, schema_health_snapshot

    scripts = [[('{"a": 1}', "stop")], [('{"a": 1}', "length")],
               [('{"b": 1}', "stop"), ('{"a": 2}', "stop")],
               [('{"b": 1}', "stop"), ('{"b": 2}', "stop")], [REFUSE]]
    with capture_schema() as counts:
        for script in scripts:
            Scripted(script).complete_json(system="s", user="u", schema=SCHEMA, purpose="t")
    d = counts.as_dict()
    assert "refused" in d and "truncated" in d, sorted(d)
    assert {k: d[k] for k in KEYS} == {"ok": 2, "repaired": 1, "failed": 1, "refused": 1,
                                       "truncated": 1}
    assert d["total"] == 5
    # tolerance: as_dict rounds repair_rate to 4 places; 0.4 is exact there
    assert d["repair_rate"] == pytest.approx(0.4, abs=1e-9)
    totals = schema_health_snapshot()["totals"]
    assert "refused" in totals and "truncated" in totals, sorted(totals)
    assert (totals["refused"], totals["truncated"], totals["total"]) == (1, 1, 5)


_KIND = st.sampled_from(["ok", "ok_cut", "repaired", "repaired_cut", "failed",
                         "failed_cut", "refused_first", "refused_repair"])
_SCRIPTS = {
    "ok": [('{"a": 1}', "stop")],
    "ok_cut": [('{"a": 1}', "length")],
    "repaired": [('{"b": 1}', "stop"), ('{"a": 2}', "stop")],
    "repaired_cut": [('{"a": ', "length"), ('{"a": 2}', "stop")],
    "failed": [('{"b": 1}', "stop"), ('{"b": 2}', "stop")],
    "failed_cut": [('{"b": 1}', "stop"), ('{"a": "x', "length")],
    "refused_first": [REFUSE],
    "refused_repair": [('{"b": 1}', "stop"), REFUSE],
}
# outcome, truncated? — from the spec Requirement for each scripted kind
_EXPECT = {"ok": ("ok", 0), "ok_cut": ("ok", 1), "repaired": ("repaired", 0),
           "repaired_cut": ("repaired", 1), "failed": ("failed", 0),
           "failed_cut": ("failed", 1), "refused_first": ("refused", 0),
           "refused_repair": ("refused", 0)}


@pytest.mark.criterion("C6")
@settings(max_examples=40, deadline=None)
@given(kinds=st.lists(_KIND, min_size=1, max_size=8))
def test_property_counts_follow_the_outcomes(kinds):
    # oracle: spec Requirement "Schema-health counts", computed in the test: each call
    # counts exactly one outcome plus truncated when cut off; total excludes truncated;
    # repair_rate = (repaired + failed) / total.
    from llm.schema_health import capture_schema

    want = dict.fromkeys(KEYS, 0)
    with capture_schema() as counts:
        for kind in kinds:
            Scripted(_SCRIPTS[kind]).complete_json(system="s", user="u", schema=SCHEMA,
                                                   purpose="t")
            outcome, cut = _EXPECT[kind]
            want[outcome] += 1
            want["truncated"] += cut
    d = counts.as_dict()
    assert {k: d.get(k) for k in KEYS} == want
    total = want["ok"] + want["repaired"] + want["failed"] + want["refused"]
    assert d["total"] == total == len(kinds)
    # tolerance: as_dict rounds repair_rate to 4 decimal places
    assert d["repair_rate"] == pytest.approx((want["repaired"] + want["failed"]) / total,
                                             abs=5e-5)
