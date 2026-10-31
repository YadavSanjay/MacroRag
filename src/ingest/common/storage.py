"""Filesystem persistence for raw bytes and parsed documents.

Layout (under the gitignored ``data/`` root):

    data/raw/ingest/<tier>/<source_id>/<safe-name>          # verbatim bytes
    data/processed/ingest/<tier>/<source_id>/<doc_id>.json  # ParsedDocument

The raw store keeps the "persist raw output before parsing" guarantee from
§4.0; the processed store holds chunk-ready JSON that the embedding/vector
modules consume downstream.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import urlsplit

from pydantic import ValidationError

from utils.objectstore import ObjectStore, object_store_from_env

from .config import StorageSettings
from .logging_utils import get_logger
from .models import ParsedDocument

log = get_logger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(value: str, max_len: int = 120) -> str:
    """Turn an arbitrary string/URL tail into a filesystem-safe token."""
    cleaned = _SAFE.sub("_", value).strip("_")
    return cleaned[:max_len] or "item"


def filename_from_url(url: str) -> str:
    tail = urlsplit(url).path.rsplit("/", 1)[-1]
    return safe_name(tail or url)


class RawStore:
    """Persists raw fetched bytes verbatim, keyed by tier/source.

    Writes go through an :class:`~utils.objectstore.ObjectStore` — the local
    filesystem in ``deploy_mode: local`` (byte-identical to before) or S3 in
    ``aws``. ``self.root`` stays the local path for the one direct-FS consumer
    (the TIC archive extractor)."""

    def __init__(self, settings: StorageSettings):
        self.root = settings.raw_dir
        self._store = object_store_from_env(settings.raw_dir, "raw/ingest")

    def save(self, tier: int, source_id: str, name: str, content: bytes) -> str:
        key = f"tier{tier}/{source_id}/{safe_name(name)}"
        self._store.put_bytes(key, content)
        log.debug("raw saved: %s (%d bytes)", key, len(content))
        return key


class DocumentStore:
    """Persists parsed documents as pretty JSON (one object per document), via an
    :class:`~utils.objectstore.ObjectStore` (local FS or S3, per ``deploy_mode``)."""

    def __init__(self, settings: StorageSettings, *, store: ObjectStore | None = None):
        self.root = settings.processed_dir
        self._store = (store if store is not None
                       else object_store_from_env(settings.processed_dir, "processed/ingest"))

    def save(self, doc: ParsedDocument) -> str:
        key = f"tier{doc.tier}/{doc.source_id}/{doc.doc_id}.json"
        self._store.put_text(key, doc.model_dump_json(indent=2, exclude_none=True))
        log.info("doc saved: %s (%d chunks, %d ts rows)",
                 key, len(doc.chunks), len(doc.timeseries))
        return key

    def iter_documents(self, prefix: str = "") -> Iterator[ParsedDocument]:
        """Yield the stored documents under ``prefix``, in key order.

        Reads through the object store, so it sees the S3 corpus in ``aws`` mode
        (the local ``root`` is empty there). An unreadable document is logged and
        skipped.

        Args:
            prefix: Key prefix, e.g. ``"tier1/dfast/"``; empty for the whole corpus.

        Yields:
            Each parsed document.
        """
        for key in sorted(self._store.iter_keys(prefix, suffix=".json")):
            text = self._store.get_text(key)
            if text is None:
                continue
            try:
                yield ParsedDocument.model_validate_json(text)
            except ValidationError as exc:
                log.warning("skipping unreadable doc %s: %s", key, exc)
