"""C1: documents chunked by the current Chunker (committed settings) are left unchanged by
rechunk: ``main(["--dry-run"])`` reports ``docs changed: 0`` and ``rechunk_doc`` returns the
same chunks (count, texts, chunk ids)."""

from __future__ import annotations

import contextlib
import io

import pytest
from hypothesis import given
from hypothesis import strategies as st

# Two consecutive paragraphs this long cannot share a window (2 * 3700 > 4000 chars), so the
# second starts a window carrying a full overlap tail: >= 319 + 2 + 3700 = 4021 chars, i.e.
# more than max_tokens (1000) estimated tokens yet within max_tokens + overlap_tokens.
LONG = st.integers(min_value=3700, max_value=3998)
FILLER = st.integers(min_value=20, max_value=6000)


def _assert_unchanged(rci, doc) -> None:
    before = rci.snapshot(doc)
    out_doc, n_before, n_after = rci.rechunk.rechunk_doc(doc.model_copy(deep=True),
                                                         rci.SETTINGS)
    assert (n_before, n_after) == (len(before), len(before))
    assert [c.chunk_id for c in out_doc.chunks] == [b[0] for b in before]
    assert [c.text for c in out_doc.chunks] == [b[2] for b in before]
    assert rci.snapshot(out_doc) == before


@pytest.mark.criterion("C1")
def test_overlapping_windows_from_chunker_are_not_resplit(rci, capsys):
    # oracle: spec Requirement/C1: three 3,800-char paragraphs give windows 3,800, then
    # tail(<=320) + 3,800 > 4,000 chars (> max_tokens) but <= 1,080 tokens, the splitter's
    # own bound, so rechunk must report 0 docs changed and return every chunk unchanged.
    doc = rci.chunked_doc("speeches_c1a", rci.body([3800, 3800, 3800]))
    assert len(doc.chunks) == 3
    assert any(rci.tokens(c.text) > rci.SETTINGS.max_tokens for c in doc.chunks)
    assert all(rci.tokens(c.text) <= rci.BOUND_TOKENS for c in doc.chunks)
    store, mem = rci.make_store(doc)
    assert rci.rechunk.main(["--dry-run"], store=store) == 0
    assert rci.docs_changed(capsys.readouterr().out) == 0
    assert rci.snapshot(rci.load(mem, doc)) == rci.snapshot(doc)
    _assert_unchanged(rci, doc)


@pytest.mark.criterion("C1")
def test_sectioned_document_from_chunker_is_not_resplit(rci, capsys):
    # oracle: spec C1 ("any body text"): a `sections` document whose second section is two
    # 3,900-char paragraphs gets an overlap window of > 4,000 chars inside that section; it
    # is the splitter's own output, so rechunk changes nothing (section metadata included).
    text = ("Overview\n\n" + rci.body([500]) + "\n\nOutlook\n\n" + rci.body([3900, 3900], 5))
    doc = rci.chunked_doc("speeches_c1b", text, strategy="sections",
                          headings=["Overview", "Outlook"])
    assert [c.metadata.section for c in doc.chunks] == ["Overview", "Outlook", "Outlook"]
    store, _ = rci.make_store(doc)
    rci.rechunk.main(["--dry-run"], store=store)
    assert rci.docs_changed(capsys.readouterr().out) == 0
    _assert_unchanged(rci, doc)


@st.composite
def _bodies(draw):
    """Paragraph-length lists for one document: random filler around at least one pair of
    consecutive long paragraphs (so an overlap window above max_tokens always exists)."""
    pre = draw(st.lists(FILLER, max_size=3))
    post = draw(st.lists(FILLER, max_size=3))
    pair = [draw(LONG), draw(LONG)]
    return pre + pair + post, draw(st.integers(min_value=0, max_value=50))


@pytest.mark.criterion("C1")
@given(specs=st.lists(_bodies(), min_size=1, max_size=3))
def test_any_chunker_output_is_left_unchanged(rci, specs):
    # oracle: spec C1: for any body text chunked by the current Chunker, rechunk is a no-op
    # (docs changed 0, same chunks), because every chunk is within the splitter's own bound.

    docs = [rci.chunked_doc(f"speeches_p{i}", rci.body(lengths, off))
            for i, (lengths, off) in enumerate(specs)]
    store, _ = rci.make_store(*docs)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rci.rechunk.main(["--dry-run"], store=store)
    assert rci.docs_changed(buf.getvalue()) == 0
    for doc in docs:
        _assert_unchanged(rci, doc)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_rewrite_run_leaves_stored_corpus_identical(rci, capsys):
    # oracle: spec Requirement (metamorphic): running rechunk for real (no --dry-run) over a
    # corpus chunked by the current Chunker must leave every stored document identical to
    # what was saved, and a second real run must also report 0 docs changed.
    docs = [rci.chunked_doc("speeches_m1", rci.body([3800, 3800, 1200, 3998], 7)),
            rci.chunked_doc("speeches_m2", rci.body([300, 3750, 3720, 3990], 2))]
    store, mem = rci.make_store(*docs)
    for _ in range(2):
        rci.rechunk.main([], store=store)
        assert rci.docs_changed(capsys.readouterr().out) == 0
        for doc in docs:
            assert rci.snapshot(rci.load(mem, doc)) == rci.snapshot(doc)
