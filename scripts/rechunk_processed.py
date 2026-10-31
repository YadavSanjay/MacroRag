"""Re-chunk already-persisted ParsedDocuments in place with the fixed budget
splitter (no re-download).

Why: earlier ingest runs used a splitter that only broke on blank lines and
never subdivided an oversized unit, so HTML bodies with single-newline
separators (speeches, podcasts, weeklies) and single mega-paragraphs landed as
one giant chunk. This walks every processed JSON and re-splits each stored
chunk's text through the current ``_split_by_budget``, preserving the chunk's
section metadata and re-numbering ``seq`` / ``chunk_id`` across the document.

Idempotent: a chunk within the splitter's own bound, ``max_tokens + overlap_tokens``
(a window plus its overlap tail), passes through unchanged, so a corpus chunked by the
current ``Chunker`` is left alone and a second run changes nothing (spec
rechunk-idempotent). Numeric docs (``chunks == []``) are skipped.

Usage:
    python scripts/rechunk_processed.py            # rewrite in place
    python scripts/rechunk_processed.py --dry-run  # report only, no writes
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

from ingest.common.chunking import _split_by_budget, estimate_tokens
from ingest.common.config import load_common_settings
from ingest.common.models import Chunk, ParsedDocument
from ingest.common.storage import DocumentStore


def resplit(text: str, s) -> list[str]:
    """Apply the budget splitter to one stored chunk's text (mirrors Chunker).

    Only a chunk above the splitter's bound is split: ``_split_by_budget`` itself emits
    windows of up to ``max_tokens`` plus an overlap tail of up to ``overlap_tokens``, so
    using ``max_tokens`` alone would re-split its own output.
    """
    if estimate_tokens(text, s.chars_per_token) <= s.max_tokens + s.overlap_tokens:
        return [text]
    return _split_by_budget(text, s.max_tokens, s.overlap_tokens, s.chars_per_token)


def rechunk_doc(doc: ParsedDocument, s) -> tuple[ParsedDocument, int, int]:
    before = len(doc.chunks)
    new: list[Chunk] = []
    seq = 0
    for ch in doc.chunks:
        for piece in resplit(ch.text, s):
            if not piece.strip():
                continue
            meta = ch.metadata.model_copy(deep=True)   # keep section + all tags
            new.append(Chunk(chunk_id=f"{doc.doc_id}::{seq:03d}", seq=seq,
                             text=piece, metadata=meta))
            seq += 1
    doc.chunks = new
    return doc, before, len(new)


def main(argv: list[str] | None = None, *, store: DocumentStore | None = None) -> int:
    """Re-chunk every stored document; ``store`` defaults to the configured corpus."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    settings = load_common_settings()
    store = store or DocumentStore(settings.storage)

    touched = added = max_before = max_after = 0
    for doc in store.iter_documents():
        if not doc.chunks:
            continue
        before_max = max(len(c.text) for c in doc.chunks)
        doc, before, after = rechunk_doc(doc, settings.chunking)
        after_max = max((len(c.text) for c in doc.chunks), default=0)
        max_before = max(max_before, before_max)
        max_after = max(max_after, after_max)
        if after != before:
            touched += 1
            added += after - before
            if not args.dry_run:
                store.save(doc)
            print(f"  {doc.doc_id[:60]:<60} {before:>3} -> {after:>3} chunks "
                  f"(max {before_max} -> {after_max} chars)")

    print(f"\n{'DRY-RUN: ' if args.dry_run else ''}docs changed: {touched} | "
          f"net chunks added: {added:+d}")
    print(f"largest chunk overall: {max_before} -> {max_after} chars "
          f"(~{max_before // 4} -> ~{max_after // 4} tokens)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
