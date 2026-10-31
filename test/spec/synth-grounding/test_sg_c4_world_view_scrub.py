"""C4: world_view_id claims in synthesize prose are rewritten to the real id."""

from __future__ import annotations

import asyncio
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from llm.sanitize import scrub_world_view_claims
from mcp_server.server import build_mcp

REAL = "macro-2026-01-02-abcdef012345"


@pytest.mark.criterion("C4")
def test_scrub_point_cases():
    # oracle: the C4 grammar applied by hand to each string (key, optional spaces,
    # ':' or '=', optional spaces, then a quoted value -> inner text replaced, quotes
    # kept; or an unquoted [A-Za-z0-9_.-] run not ending in '.' -> run replaced).
    cases = [
        ('Pinned to world_view_id: "taiwan_strait_blockade_2026" for audit.',
         f'Pinned to world_view_id: "{REAL}" for audit.'),
        ("see world_view_id=taiwan.v2-x and more",
         f"see world_view_id={REAL} and more"),
        ('{"world_view_id": "fake"}', f'{{"world_view_id": "{REAL}"}}'),
        ("WORLD_VIEW_ID = 'abc'", f"WORLD_VIEW_ID = '{REAL}'"),
        ("`World_View_Id`:`zzz` ok", f"`World_View_Id`:`{REAL}` ok"),
        ("a world_view_id:x1 b world_view_id = \"y 2\" c",
         f"a world_view_id:{REAL} b world_view_id = \"{REAL}\" c"),
        ("pin results to the world_view_id for reproducibility",
         "pin results to the world_view_id for reproducibility"),
        (f"world_view_id: {REAL} (already right)", f"world_view_id: {REAL} (already right)"),
    ]
    for text, expected in cases:
        assert scrub_world_view_claims(text, REAL) == expected, text


@pytest.mark.criterion("C4")
def test_scrub_none_reads_unavailable_and_plain_text_untouched():
    # oracle: C4 grammar: replacement is the literal "unavailable" when the id is
    # None; text without the key is returned unchanged.
    assert (scrub_world_view_claims('world_view_id: "taiwan_x"', None)
            == 'world_view_id: "unavailable"')
    assert (scrub_world_view_claims("world_view_id=taiwan_x.", None)
            == "world_view_id=unavailable.")
    assert (scrub_world_view_claims("Rates rise 35 bp; spreads widen.", REAL)
            == "Rates rise 35 bp; spreads widen.")


@pytest.mark.criterion("C4")
def test_scrub_boundary_cases_of_the_grammar():
    # oracle: the amended C4 grammar applied by hand, one rule per case:
    #  - an unquoted value does not end in "." (the full stop is kept);
    #  - spaces or tabs around ':'/'=' are allowed, a newline never is;
    #  - a quoted value may be empty, and never spans a newline;
    #  - the key must not be preceded by [A-Za-z0-9_];
    #  - an unmatched quote is not part of the key;
    #  - ':'/'=' followed by neither value form is not a claim.
    cases = [
        # trailing full stop(s) are not part of an unquoted value
        ("world_view_id: macro-x.", f"world_view_id: {REAL}."),
        ("ends world_view_id=a..b.. here", f"ends world_view_id={REAL}.. here"),
        # dots only: no run that doesn't end in "." -> not a claim
        ("world_view_id: ...", "world_view_id: ..."),
        # tabs allowed on either side of the separator
        ("world_view_id\t:\t'x'", f"world_view_id\t:\t'{REAL}'"),
        ("world_view_id \t= \tabc", f"world_view_id \t= \t{REAL}"),
        # a newline is never optional whitespace -> not a claim
        ("world_view_id:\nabc", "world_view_id:\nabc"),
        ("world_view_id\n: abc", "world_view_id\n: abc"),
        # empty quoted value is a claim
        ('world_view_id: ""', f'world_view_id: "{REAL}"'),
        ("world_view_id=''", f"world_view_id='{REAL}'"),
        # quoted value spanning a newline is neither value form -> not a claim
        ('world_view_id: "a\nb"', 'world_view_id: "a\nb"'),
        # key preceded by a word character -> not a claim
        ("my_world_view_id: x", "my_world_view_id: x"),
        ("Xworld_view_id=x", "Xworld_view_id=x"),
        ("9world_view_id: 'x'", "9world_view_id: 'x'"),
        # key preceded by a non-word character -> claim
        ("(world_view_id: x)", f"(world_view_id: {REAL})"),
        ("-world_view_id=x", f"-world_view_id={REAL}"),
        # unmatched leading quote: not part of the key, the key itself starts a claim
        ('"world_view_id: x', f'"world_view_id: {REAL}'),
        # unmatched trailing quote: the key is followed by the quote, not ':'/'='
        ("world_view_id': x", "world_view_id': x"),
        ("\"world_view_id': x", "\"world_view_id': x"),
        # separator followed by neither value form -> not a claim
        ("world_view_id: , next", "world_view_id: , next"),
        ("world_view_id = (none)", "world_view_id = (none)"),
    ]
    for text, expected in cases:
        assert scrub_world_view_claims(text, REAL) == expected, repr(text)


# ── generated texts with a hand-built expected rewrite ───────────────────────

_FILLER = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ABCXYZ0123456789,;!?()",
                  max_size=20)
# Unquoted value = a core run not ending in "." plus optional trailing dots, which
# the C4 grammar keeps outside the value.
_CORE = st.text(alphabet="abcdefghijXYZ0123456789_.-", min_size=1,
                max_size=15).filter(lambda s: not s.endswith("."))
_WS = st.sampled_from(["", " ", "  ", "\t", " \t"])
_ID = st.one_of(st.none(),
                st.text(alphabet="abcdefxyz0123456789-", min_size=1, max_size=20))


@st.composite
def _key(draw):
    case = draw(st.lists(st.booleans(), min_size=13, max_size=13))
    return "".join(c.upper() if u else c for c, u in zip("world_view_id", case))


@st.composite
def _claim(draw, wv):
    """(input claim, expected claim) built from the C4 grammar."""
    key = draw(_key())
    kq = draw(st.sampled_from(["", '"', "'", "`"]))
    sep = draw(_WS) + draw(st.sampled_from([":", "="])) + draw(_WS)
    head = f"{kq}{key}{kq}{sep}"
    replacement = "unavailable" if wv is None else wv
    vq = draw(st.sampled_from(["", '"', "'", "`"]))
    if vq:          # quoted value, possibly empty
        inner = draw(st.text(alphabet="abc XYZ_.-019", max_size=15))
        return f"{head}{vq}{inner}{vq}", f"{head}{vq}{replacement}{vq}"
    core, dots = draw(_CORE), draw(st.sampled_from(["", ".", ".."]))
    return f"{head}{core}{dots}", f"{head}{replacement}{dots}"


@st.composite
def _non_claim(draw):
    """A key mention the C4 grammar does not treat as a claim (left unchanged)."""
    key, core = draw(_key()), draw(_CORE)
    sep = draw(_WS) + draw(st.sampled_from([":", "="])) + draw(_WS)
    kind = draw(st.sampled_from(["word_prefix", "newline_before", "newline_after",
                                 "unmatched_trailing_quote", "no_value"]))
    if kind == "word_prefix":     # key preceded by [A-Za-z0-9_]
        return draw(st.sampled_from(list("aZ9_"))) + f"{key}{sep}{core}"
    if kind == "newline_before":  # newline is never optional whitespace
        return f"{key}\n{sep}{core}"
    if kind == "newline_after":
        return f"{key}{sep}\n{core}"
    if kind == "unmatched_trailing_quote":
        quote = draw(st.sampled_from(['"', "'", "`"]))
        return f"{key}{quote}{sep}{core}"
    return f"{key}{sep}"          # followed by " ; " or end of text: no value form


@st.composite
def _text_and_expected(draw):
    wv = draw(_ID)
    parts_in, parts_out = [], []
    for _ in range(draw(st.integers(min_value=1, max_value=4))):
        filler = draw(_FILLER)
        parts_in.append(filler)
        parts_out.append(filler)
        piece = draw(st.sampled_from(["claim", "non_claim", "none"]))
        if piece == "claim":
            claim_in, claim_out = draw(_claim(wv))
            parts_in.append(claim_in)
            parts_out.append(claim_out)
        elif piece == "non_claim":
            mention = draw(_non_claim())
            parts_in.append(mention)
            parts_out.append(mention)
    # " ; " separators keep every claim's key and unquoted value delimited.
    return wv, " ; ".join(parts_in), " ; ".join(parts_out)


@pytest.mark.criterion("C4")
@given(case=_text_and_expected())
def test_scrub_matches_grammar_and_is_idempotent(case):
    # oracle: the expected text is assembled piecewise from the C4 grammar (fillers
    # contain no "_" so cannot hold the key; claims rewritten by hand, trailing dots
    # of unquoted values and every non-claim mention kept verbatim); idempotence
    # and "every other character unchanged" are stated in C4.
    wv, text, expected = case
    once = scrub_world_view_claims(text, wv)
    assert once == expected
    assert scrub_world_view_claims(once, wv) == once


@pytest.mark.criterion("C4")
@given(text=st.text(min_size=1, max_size=60).filter(
           lambda s: "world_view_id" not in s.lower()),
       wv=_ID)
def test_scrub_is_identity_without_the_key(text, wv):
    # oracle: C4: identity on text containing no `world_view_id` (case-insensitive).
    assert scrub_world_view_claims(text, wv) == text


# ── pipeline + MCP surface ───────────────────────────────────────────────────

_FAKE_SUMMARY = ('Scenario pinned to world_view_id: "taiwan_strait_blockade_2026" '
                 "as requested; pin results to the world_view_id.")
_FAKE_DETAIL = "Equities fall (world_view_id=taiwan.fake-1)."


def _responder(cite):
    def responder(system, user):
        if "SHOCK VECTOR" in user:
            return json.dumps({"shock_vector": {}})
        return json.dumps({"summary": _FAKE_SUMMARY,
                           "details": [{"text": _FAKE_DETAIL, "chunk_ids": [cite]}],
                           "unanswered": []})
    return responder


QUERY = "how would markets move under this shock; return world_view_id"


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_synthesize_prose_carries_real_id_rest_and_mcp(tmp_path, make_pipeline, sg):
    # oracle: the real id comes from the fixture manifest (built_at 2026-01-02,
    # digest abcdef012345 -> "macro-2026-01-02-abcdef012345" per the documented
    # world_view_id form); the rewritten prose is the C4 grammar applied by hand to
    # the scripted summary/detail. Metamorphic: the MCP body's prose equals the
    # pipeline's (same deterministic pipeline).
    cite = sg.chunk_id(sg.EBA)
    pipe, s = make_pipeline(tmp_path, _responder(cite))
    res = pipe.synthesize(QUERY)
    assert res.world_view_id == sg.FIXED_WORLD_VIEW_ID
    want_summary = (f'Scenario pinned to world_view_id: "{sg.FIXED_WORLD_VIEW_ID}" '
                    "as requested; pin results to the world_view_id.")
    want_detail = f"Equities fall (world_view_id={sg.FIXED_WORLD_VIEW_ID})."
    assert res.query.answer.summary == want_summary
    assert [d.text for d in res.query.answer.details] == [want_detail]

    mcp = build_mcp(lambda: pipe, s)
    body = json.loads(asyncio.run(mcp.call_tool(
        "macro_synthesize_shock_vector", {"question": QUERY}))[0].text)
    assert body["summary"] == want_summary
    assert [d["text"] for d in body["details"]] == [want_detail]
    assert body["world_view_id"] == sg.FIXED_WORLD_VIEW_ID


@pytest.mark.criterion("C4")
def test_synthesize_without_manifest_claims_unavailable(tmp_path, make_pipeline, sg):
    # oracle: with no manifest the response carries no real id; C4 then rewrites
    # every claimed value to the literal "unavailable" (grammar applied by hand).
    pipe, _ = make_pipeline(tmp_path, _responder(sg.chunk_id(sg.EBA)),
                            write_fixed_manifest=False)
    res = pipe.synthesize(QUERY)
    assert res.world_view_id is None
    assert res.query.answer.summary == (
        'Scenario pinned to world_view_id: "unavailable" '
        "as requested; pin results to the world_view_id.")
    assert [d.text for d in res.query.answer.details] == [
        "Equities fall (world_view_id=unavailable)."]
