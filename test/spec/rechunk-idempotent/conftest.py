"""Fixtures for the rechunk-idempotent spec tests.

Turns the repo dotenv file off before anything from ``src`` is imported, and puts ``src`` and
``scripts`` (not a package) on ``sys.path`` so the tests can ``import rechunk_processed`` as
``test/test_curate_objectstore.py`` does.

Chunking settings are the committed ones (``load_common_settings().chunking``, i.e.
``configs/ingest/common.yaml``: 1000 tokens, 80 overlap, 4 chars per token). Documents live in
a ``DocumentStore`` over an ``InMemoryObjectStore`` (the object-store fake); nothing is read
from or written to disk. All text is synthetic. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import os
import re
import sys
import types
from datetime import date
from pathlib import Path

import pytest

# Before any import from src: never load the repo's secrets dotenv file.
os.environ["MACRORAG_DOTENV"] = "0"

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT / "src", _ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import rechunk_processed  # noqa: E402  (needs scripts on sys.path, set just above)

from ingest.common.chunking import Chunker, estimate_tokens  # noqa: E402
from ingest.common.config import StorageSettings, load_common_settings  # noqa: E402
from ingest.common.models import Chunk, ChunkMetadata, ParsedDocument  # noqa: E402
from ingest.common.storage import DocumentStore  # noqa: E402
from utils.objectstore import InMemoryObjectStore  # noqa: E402

SETTINGS = load_common_settings().chunking
# The splitter's own bound (spec Requirement): max_tokens + overlap_tokens estimated tokens,
# i.e. that many tokens times chars_per_token characters (spec C2).
BOUND_TOKENS = SETTINGS.max_tokens + SETTINGS.overlap_tokens
BOUND_CHARS = BOUND_TOKENS * SETTINGS.chars_per_token

# Synthetic vocabulary; every ninth word closes a sentence so long paragraphs have
# sentence boundaries the splitter can use.
_WORDS = ("rates", "inflation", "payrolls", "spreads", "credit", "liquidity", "yields",
          "housing", "energy", "chips", "demand", "supply", "growth", "policy", "balance")


def paragraph(length: int, offset: int = 0) -> str:
    """A synthetic paragraph of exactly ``length`` characters (``length`` >= 2), no blank
    lines inside, starting with a word and ending with a full stop."""
    parts: list[str] = []
    size = 0
    i = offset
    while size < length + 20:
        word = _WORDS[i % len(_WORDS)]
        if (i - offset) % 9 == 8:
            word += "."
        parts.append(word)
        size += len(word) + 1
        i += 1
    text = " ".join(parts)[: length - 1]
    return text + "."


def body(lengths: list[int], offset: int = 0) -> str:
    """Paragraphs of the given lengths joined by blank lines."""
    return "\n\n".join(paragraph(n, offset + 3 * k) for k, n in enumerate(lengths))


def meta(section: str | None = None, factor: list[str] | None = None) -> ChunkMetadata:
    return ChunkMetadata(publisher="Synthetic Bank", publication="Synthetic Review",
                         release_date=date(2026, 9, 1), retrieval_date=date(2026, 10, 1),
                         url="https://example.invalid/doc", content_hash="hash",
                         tier=1, bucket="A", doc_type="speech", section=section,
                         factor_tags=list(factor or []))


def make_doc(doc_id: str, chunks: list[Chunk]) -> ParsedDocument:
    return ParsedDocument(doc_id=doc_id, source_id="speeches", title=doc_id,
                          doc_type="speech", publisher="Synthetic Bank",
                          publication="Synthetic Review", release_date=date(2026, 9, 1),
                          retrieval_date=date(2026, 10, 1),
                          url="https://example.invalid/doc", content_hash="hash", tier=1,
                          bucket="A", chunks=chunks)


def chunked_doc(doc_id: str, text: str, strategy: str = "paragraphs",
                headings: list[str] | None = None) -> ParsedDocument:
    """A document whose chunks come from the current Chunker with the committed settings."""
    chunks = Chunker(SETTINGS).chunk(text, doc_id=doc_id, base_metadata=meta(factor=["rates"]),
                                     strategy=strategy, section_headings=headings)
    return make_doc(doc_id, chunks)


def stored_doc(doc_id: str, texts_sections: list[tuple[str, str | None]]) -> ParsedDocument:
    """A document stored with the given (text, section) chunks, seq/ids numbered in order."""
    chunks = [Chunk(chunk_id=f"{doc_id}::{i:03d}", seq=i, text=t,
                    metadata=meta(sec, ["credit", f"tag{i}"]))
              for i, (t, sec) in enumerate(texts_sections)]
    return make_doc(doc_id, chunks)


def make_store(*docs: ParsedDocument) -> tuple[DocumentStore, InMemoryObjectStore]:
    mem = InMemoryObjectStore()
    store = DocumentStore(StorageSettings(data_dir="spec-unused-store"), store=mem)
    for d in docs:
        store.save(d)
    return store, mem


def load(mem: InMemoryObjectStore, d: ParsedDocument) -> ParsedDocument:
    text = mem.get_text(f"tier{d.tier}/{d.source_id}/{d.doc_id}.json")
    assert text is not None
    return ParsedDocument.model_validate_json(text)


def docs_changed(out: str) -> int:
    """The N in main()'s ``docs changed: N`` line."""
    m = re.search(r"docs changed: (\d+)", out)
    assert m is not None, out
    return int(m.group(1))


def snapshot(doc: ParsedDocument) -> list[tuple[str, int, str, dict]]:
    """Comparable view of a document's chunks: (chunk_id, seq, text, metadata)."""
    return [(c.chunk_id, c.seq, c.text, c.metadata.model_dump()) for c in doc.chunks]


def tokens(text: str) -> int:
    return estimate_tokens(text, SETTINGS.chars_per_token)


@pytest.fixture(scope="session")
def rci():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        SETTINGS=SETTINGS, BOUND_TOKENS=BOUND_TOKENS, BOUND_CHARS=BOUND_CHARS,
        rechunk=rechunk_processed, paragraph=paragraph, body=body, meta=meta,
        make_doc=make_doc, chunked_doc=chunked_doc, stored_doc=stored_doc,
        make_store=make_store, load=load, docs_changed=docs_changed, snapshot=snapshot,
        tokens=tokens)
