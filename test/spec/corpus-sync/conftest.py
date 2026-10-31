"""Fixtures for the corpus-sync spec tests.

In-memory object stores (the S3 fake) that record writes and deletes. No network,
no ``data/``, no clock.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# E402 below: this import needs src on sys.path, set just above.
from utils.objectstore import InMemoryObjectStore  # noqa: E402


class RecordingStore(InMemoryObjectStore):
    """The in-memory object store, recording writes and deletes (an I/O-boundary fake)."""

    def __init__(self, contents: dict[str, bytes] | None = None) -> None:
        super().__init__()
        for k, v in (contents or {}).items():
            super().put_bytes(k, v)
        self.writes: list[str] = []
        self.deletes: list[str] = []

    def put_bytes(self, key: str, data: bytes) -> None:
        self.writes.append(key)
        super().put_bytes(key, data)

    def delete(self, key: str) -> None:
        self.deletes.append(key)
        super().delete(key)


def snapshot(store: InMemoryObjectStore) -> dict[str, bytes | None]:
    """Every key's bytes."""
    return {k: store.get_bytes(k) for k in store.iter_keys("")}


COUNT_KEYS = {"copied", "identical", "conflict", "overwritten", "dst_only"}


@pytest.fixture(scope="session")
def cs():
    return types.SimpleNamespace(RecordingStore=RecordingStore, snapshot=snapshot,
                                 COUNT_KEYS=COUNT_KEYS)
