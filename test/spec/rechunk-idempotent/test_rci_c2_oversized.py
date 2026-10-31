"""C2: a stored chunk longer than (max_tokens + overlap_tokens) * chars_per_token characters
is split into >= 2 pieces, each at most that long, section metadata kept and seq / chunk_id
renumbered across the document; a second ``rechunk_doc`` changes nothing."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

LONG = st.integers(min_value=3700, max_value=3998)
# Filler paragraphs stay <= 3,998 chars so every piece (overlap tail <= 320 + 2 + atom) is
# <= 4,320 chars, the character form of the bound spec C2 states.
FILLER = st.integers(min_value=20, max_value=3998)


def _rechunk(rci, doc):
    out, _, _ = rci.rechunk.rechunk_doc(doc.model_copy(deep=True), rci.SETTINGS)
    return out


def _assert_numbered(rci, doc) -> None:
    assert [c.seq for c in doc.chunks] == list(range(len(doc.chunks)))
    assert [c.chunk_id for c in doc.chunks] == [
        f"{doc.doc_id}::{i:03d}" for i in range(len(doc.chunks))]


@pytest.mark.criterion("C2")
def test_oversized_chunk_split_in_bound_and_stable(rci):
    # oracle: spec C2 with the committed settings (bound 1,080 tokens = 4,320 chars): the
    # 11,404-char middle chunk (three 3,800-char paragraphs) is above the bound so it becomes
    # >= 2 pieces, each <= 4,320 chars, carrying section "Outlook" and its tags; the 600-char
    # and 4,200-char (within bound) neighbours pass through unchanged; seq / ids run 0..n-1;
    # a second rechunk_doc returns exactly the same chunks.
    big = rci.body([3800, 3800, 3800])
    first, last = rci.paragraph(600), rci.paragraph(4200, 4)
    assert len(big) > rci.BOUND_CHARS and len(last) <= rci.BOUND_CHARS
    doc = rci.stored_doc("speeches_c2a", [(first, "Intro"), (big, "Outlook"), (last, None)])
    out = _rechunk(rci, doc)
    texts = [c.text for c in out.chunks]
    assert texts[0] == first and texts[-1] == last
    middle = out.chunks[1:-1]
    assert len(middle) >= 2
    assert all(len(c.text) <= rci.BOUND_CHARS for c in out.chunks)
    assert all(c.metadata.section == "Outlook" for c in middle)
    assert all(c.metadata.factor_tags == ["credit", "tag1"] for c in middle)
    assert out.chunks[-1].metadata.factor_tags == ["credit", "tag2"]
    _assert_numbered(rci, out)
    again = _rechunk(rci, out)
    assert rci.snapshot(again) == rci.snapshot(out)


@pytest.mark.criterion("C2")
def test_rewrite_run_splits_oversized_then_is_idempotent(rci, capsys):
    # oracle: spec Requirement/C2: a real run splits the one oversized document (1 doc
    # changed) into in-bound pieces; a second real run reports 0 docs changed and leaves the
    # stored document identical.
    doc = rci.stored_doc("speeches_c2b", [(rci.body([3900, 3900, 3900, 300], 9), "Risks")])
    store, mem = rci.make_store(doc)
    rci.rechunk.main([], store=store)
    assert rci.docs_changed(capsys.readouterr().out) == 1
    once = rci.load(mem, doc)
    assert len(once.chunks) >= 2
    assert all(len(c.text) <= rci.BOUND_CHARS for c in once.chunks)
    assert all(c.metadata.section == "Risks" for c in once.chunks)
    rci.rechunk.main([], store=store)
    assert rci.docs_changed(capsys.readouterr().out) == 0
    assert rci.snapshot(rci.load(mem, doc)) == rci.snapshot(once)


@st.composite
def _oversized(draw):
    """Paragraph lengths for one oversized stored chunk: filler around a pair of consecutive
    long paragraphs (so the chunk exceeds the bound and is split with an overlap window)."""
    pre = draw(st.lists(FILLER, max_size=3))
    post = draw(st.lists(FILLER, max_size=3))
    return pre + [draw(LONG), draw(LONG)] + post, draw(st.integers(min_value=0, max_value=50))


@pytest.mark.criterion("C2")
@given(spec=_oversized(), small=st.integers(min_value=20, max_value=4320))
def test_any_oversized_chunk_splits_in_bound_idempotently(rci, spec, small):
    # oracle: spec C2: for any chunk above 4,320 chars: >= 2 pieces, each <= 4,320 chars,
    # metadata kept, seq / ids renumbered; an in-bound neighbour (<= 4,320 chars) is kept
    # as is; and rechunk_doc on the result is the identity.
    lengths, off = spec
    big = rci.body(lengths, off)
    assert len(big) > rci.BOUND_CHARS
    neighbour = rci.paragraph(small, off)
    doc = rci.stored_doc("speeches_p", [(neighbour, "Before"), (big, "Main")])
    out = _rechunk(rci, doc)
    assert out.chunks[0].text == neighbour
    assert out.chunks[0].metadata.section == "Before"
    pieces = out.chunks[1:]
    assert len(pieces) >= 2
    assert all(len(c.text) <= rci.BOUND_CHARS for c in pieces)
    assert all(c.metadata.section == "Main" for c in pieces)
    assert all(c.metadata.factor_tags == ["credit", "tag1"] for c in pieces)
    _assert_numbered(rci, out)
    assert rci.snapshot(_rechunk(rci, out)) == rci.snapshot(out)
