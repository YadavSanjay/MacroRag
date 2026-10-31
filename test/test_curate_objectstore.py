"""Curation scripts read the processed corpus through the object store.

In ``deploy_mode: aws`` the processed corpus lives in S3 and the local
``processed_dir`` on the instance is empty. The curate tasks (``POST /admin/curate``)
used to glob that local directory, so on AWS they finished having read nothing.
These tests run each script's core against an in-memory object store (the S3 fake)
and check that the documents in the store are what get read and rewritten.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from ingest.common.config import StorageSettings
from ingest.common.models import Chunk, ChunkMetadata, ParsedDocument
from ingest.common.storage import DocumentStore
from utils.config import RagSettings
from utils.objectstore import InMemoryObjectStore

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import backup_restore  # noqa: E402
import normalize_vocab  # noqa: E402
import rechunk_processed  # noqa: E402
import reextract_dfast  # noqa: E402


def _doc(source_id: str, n: int, *, tier: int = 1, texts: list[str] | None = None,
         factor: list[str] | None = None, entity: list[str] | None = None,
         doc_type: str = "speech", timeseries: list[dict] | None = None) -> ParsedDocument:
    did = f"{source_id}_doc{n}"
    meta = dict(publisher="Fed", publication=f"Doc {n}", release_date=date(2026, 9, n),
                retrieval_date=date(2026, 10, 1), url=f"https://example.invalid/{n}",
                content_hash=f"hash{n}", tier=tier, bucket="A", doc_type=doc_type)
    return ParsedDocument(
        doc_id=did, source_id=source_id, title=f"Doc {n}", doc_type=doc_type,
        publisher="Fed", publication=f"Doc {n}", release_date=date(2026, 9, n),
        retrieval_date=date(2026, 10, 1), url=meta["url"], content_hash=f"hash{n}",
        tier=tier, bucket="A", timeseries=list(timeseries or []),
        chunks=[Chunk(chunk_id=f"{did}::{i:03d}", seq=i, text=t,
                      metadata=ChunkMetadata(**meta, factor_tags=list(factor or []),
                                             entity_tags=list(entity or [])))
                for i, t in enumerate(texts or ["Short text."])])


def _store(tmp_path: Path, *docs: ParsedDocument) -> tuple[DocumentStore, InMemoryObjectStore]:
    """A DocumentStore over an in-memory object store, seeded with ``docs``.

    The local ``processed_dir`` points at an empty temp dir, as on an AWS instance.
    """
    mem = InMemoryObjectStore()
    settings = StorageSettings(raw_dir=str(tmp_path / "raw"),
                               processed_dir=str(tmp_path / "processed"))
    store = DocumentStore(settings, store=mem)
    for d in docs:
        store.save(d)
    return store, mem


def _load(mem: InMemoryObjectStore, d: ParsedDocument) -> ParsedDocument:
    text = mem.get_text(f"tier{d.tier}/{d.source_id}/{d.doc_id}.json")
    assert text is not None
    return ParsedDocument.model_validate_json(text)


def test_document_store_iterates_the_injected_store_in_key_order(tmp_path):
    b, a, c = _doc("speeches", 2), _doc("speeches", 1), _doc("dfast", 3)
    store, _ = _store(tmp_path, b, a, c)
    assert [d.doc_id for d in store.iter_documents()] == [
        "dfast_doc3", "speeches_doc1", "speeches_doc2"]
    assert [d.doc_id for d in store.iter_documents("tier1/speeches/")] == [
        "speeches_doc1", "speeches_doc2"]
    assert not list((tmp_path / "processed").rglob("*.json"))   # nothing on local disk


def test_rechunk_reads_and_rewrites_the_object_store(tmp_path):
    # 1000-token budget at 4 chars/token (configs/ingest/common.yaml): a 12,000-char
    # unit is ~3,000 tokens, so the splitter must cut it into several chunks.
    big = _doc("speeches", 1, texts=[("word " * 2400).strip()])
    store, mem = _store(tmp_path, big)
    assert rechunk_processed.main([], store=store) == 0
    assert len(_load(mem, big).chunks) > 1


def test_rechunk_dry_run_writes_nothing(tmp_path):
    big = _doc("speeches", 1, texts=[("word " * 2400).strip()])
    store, mem = _store(tmp_path, big)
    rechunk_processed.main(["--dry-run"], store=store)
    assert len(_load(mem, big).chunks) == 1


def test_normalize_vocab_canonicalizes_tags_in_the_object_store(tmp_path):
    # Aliases from configs/ingest/vocabulary.yaml (normalize_vocab docstring): AI -> ai.
    d = _doc("speeches", 1, factor=["AI"])
    store, mem = _store(tmp_path, d)
    assert normalize_vocab.main([], store=store) == 0
    assert _load(mem, d).chunks[0].metadata.factor_tags == ["ai"]


def test_strip_legacy_grid_reads_dfast_docs_from_the_object_store(tmp_path):
    grid = {"header": ["a"], "cells": [["1"]]}
    long_row = {"variable": "unemployment", "period": "2026-Q1", "value": 4.1}
    d = _doc("dfast", 1, doc_type="dfast_scenarios", timeseries=[grid, long_row])
    other = _doc("speeches", 2, timeseries=[grid])          # not under tier1/dfast/
    store, mem = _store(tmp_path, d, other)
    reextract_dfast.strip_legacy_grid(store=store)
    assert _load(mem, d).timeseries == [long_row]
    assert _load(mem, other).timeseries == [grid]


def test_backup_in_aws_mode_says_the_corpus_is_in_s3(capsys):
    settings = RagSettings(deploy_mode="aws")
    items = backup_restore._backup_items(settings)
    assert settings.manifest_path not in items      # the live manifest is the S3 one
    assert settings.timeseries_path in items        # SQLite stays on local disk
    assert "s3" in capsys.readouterr().out.lower()


def test_backup_in_local_mode_keeps_the_manifest():
    settings = RagSettings()
    assert settings.manifest_path in backup_restore._backup_items(settings)
