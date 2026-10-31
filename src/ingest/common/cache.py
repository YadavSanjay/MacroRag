"""Conditional-GET / content-hash cache (Ingestion Design §2.8 "Polite caching").

Persists ``(url, etag, last_modified, content_hash, fetched_at)`` in a small
SQLite table so re-runs only refetch on ``ETag`` / ``If-Modified-Since`` mismatch,
and Stage A discovery can cheaply detect "nothing changed" via a body hash.

SQLite (stdlib) is intentionally chosen over an extra dependency; the access
pattern is low-volume and single-process per run.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class CacheEntry:
    url: str
    etag: str | None
    last_modified: str | None
    content_hash: str | None
    fetched_at: str | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS fetch_cache (
    url           TEXT PRIMARY KEY,
    etag          TEXT,
    last_modified TEXT,
    content_hash  TEXT,
    fetched_at    TEXT
);
CREATE TABLE IF NOT EXISTS seen_documents (
    doc_url       TEXT PRIMARY KEY,
    source_id     TEXT,
    content_hash  TEXT,
    processed_at  TEXT
);
"""


class FetchCache:
    """Thread-safe-enough SQLite cache for HTTP validators + processed docs."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── HTTP validators (per-URL ETag / Last-Modified / body hash) ───────────

    def get(self, url: str) -> CacheEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT url, etag, last_modified, content_hash, fetched_at "
                "FROM fetch_cache WHERE url = ?",
                (url,),
            ).fetchone()
        return CacheEntry(*row) if row else None

    def put(
        self,
        url: str,
        *,
        etag: str | None,
        last_modified: str | None,
        content_hash: str | None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO fetch_cache (url, etag, last_modified, content_hash, fetched_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET "
                "etag=excluded.etag, last_modified=excluded.last_modified, "
                "content_hash=excluded.content_hash, fetched_at=excluded.fetched_at",
                (url, etag, last_modified, content_hash, now),
            )
            self._conn.commit()

    # ── Processed-document ledger (Stage B idempotency) ──────────────────────

    def is_processed(self, doc_url: str, content_hash: str) -> bool:
        """True if this content URL was already parsed with the same body hash."""
        with self._lock:
            row = self._conn.execute(
                "SELECT content_hash FROM seen_documents WHERE doc_url = ?",
                (doc_url,),
            ).fetchone()
        return bool(row) and row[0] == content_hash

    def mark_processed(self, doc_url: str, source_id: str, content_hash: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO seen_documents (doc_url, source_id, content_hash, processed_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(doc_url) DO UPDATE SET "
                "content_hash=excluded.content_hash, processed_at=excluded.processed_at",
                (doc_url, source_id, content_hash, now),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
