"""sync_objects refuses to replace a destination key it can list but not read.

S3ObjectStore.get_bytes maps every client error (AccessDenied included) to None, so
without this guard an unreadable object would be counted as missing and overwritten.
"""

from __future__ import annotations

import pytest

from utils.corpus_sync import sync_objects
from utils.objectstore import InMemoryObjectStore


class _UnreadableStore(InMemoryObjectStore):
    """Lists its keys but every read comes back None, as S3 does on AccessDenied."""

    def get_bytes(self, key: str) -> bytes | None:
        return None


def test_listed_but_unreadable_destination_key_raises():
    src, dst = InMemoryObjectStore(), _UnreadableStore()
    src.put_bytes("tier1/a/1.json", b"new")
    dst.put_bytes("tier1/a/1.json", b"old")
    with pytest.raises(RuntimeError, match="unreadable"):
        sync_objects(src, dst)
    assert dst._data["tier1/a/1.json"] == b"old"
