"""Fixtures for the retag-endpoint spec tests.

Hand-built ``ParsedDocument``s written as the processed doc store writes them, into an
in-memory object store that counts writes. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# E402 below: these imports need src on sys.path, set just above.
from ingest.common.models import Chunk, ChunkMetadata, ParsedDocument  # noqa: E402
from utils.objectstore import InMemoryObjectStore  # noqa: E402

REPO_ROOT = _ROOT
OLD_FACTOR = ["discount_rate", "rates_vol", "forward_guidance"]
NEW_FACTOR = ["discount_rate", "rates_vol", "forward_guidance", "inflation", "growth", "labor"]


class CountingStore(InMemoryObjectStore):
    """The in-memory object store, counting writes (an I/O-boundary fake)."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def put_bytes(self, key: str, data: bytes) -> None:
        self.writes.append(key)
        super().put_bytes(key, data)


def doc(source_id: str, n: int, *, tier: int = 1, chunks: int = 2,
        factor: list[str] | None = None, entity: list[str] | None = None,
        section: str | None = "Remarks") -> ParsedDocument:
    """A processed document with ``chunks`` chunks carrying the given tags."""
    did = f"{source_id}_2026-09-0{n}_doc{n}.htm"
    meta = dict(publisher="Board of Governors of the Federal Reserve System",
                publication=f"Speech {n}", release_date=date(2026, 9, n),
                retrieval_date=date(2026, 10, 1), url=f"https://example.invalid/{n}",
                content_hash=f"hash{n}", tier=tier, bucket="A", doc_type="speech",
                section=section, extra={"speaker": "Waller"})
    return ParsedDocument(
        doc_id=did, source_id=source_id, title=f"Speech {n}", doc_type="speech",
        publisher=meta["publisher"], publication=meta["publication"],
        release_date=date(2026, 9, n), retrieval_date=date(2026, 10, 1),
        url=meta["url"], content_hash=f"hash{n}", tier=tier, bucket="A",
        fields={"speaker": "Waller"},
        chunks=[Chunk(chunk_id=f"{did}::{i:03d}", seq=i, text=f"Text {n}.{i} on policy.",
                      metadata=ChunkMetadata(**meta,
                                             factor_tags=list(factor or OLD_FACTOR),
                                             entity_tags=list(entity or [])))
                for i in range(chunks)])


def key(d: ParsedDocument) -> str:
    return f"tier{d.tier}/{d.source_id}/{d.doc_id}.json"


def put(store, d: ParsedDocument) -> None:
    """Write as the processed doc store does."""
    store.put_text(key(d), d.model_dump_json(indent=2, exclude_none=True))


def load(store, k: str) -> ParsedDocument:
    return ParsedDocument.model_validate_json(store.get_text(k))


def without_tags(d: ParsedDocument) -> dict:
    """The document as a dict with every chunk's tags removed."""
    out = d.model_dump(mode="json")
    for c in out["chunks"]:
        c["metadata"].pop("factor_tags")
        c["metadata"].pop("entity_tags")
    return out


@pytest.fixture(scope="session")
def rt():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        REPO_ROOT=REPO_ROOT, OLD_FACTOR=OLD_FACTOR, NEW_FACTOR=NEW_FACTOR,
        CountingStore=CountingStore, doc=doc, key=key, put=put, load=load,
        without_tags=without_tags)
