"""Object storage abstraction — the single seam that lets the corpus object data
(raw bytes, processed ``ParsedDocument`` JSON, index manifest) live either on the
local filesystem (``deploy_mode: local``, unchanged) or in S3 (``deploy_mode: aws``).

Design (docs/design/Macro_RAG_AWS_Deployment.md):

* Keys are POSIX-style relative paths (``tier1/<source>/<doc>.json``) — the same
  layout in both backends. A store is *rooted*: a :class:`LocalObjectStore` at a
  ``data/…`` directory, an :class:`S3ObjectStore` at ``s3://bucket/{prefix}``.
* Only object-shaped corpus data flows through here. SQLite (time-series + ingest
  cache) and JSONL traces/eval stay on the local/EBS filesystem — they are
  single-writer / append-shaped and untouched by this module.
* Local mode is **byte-identical** to the previous direct-``pathlib`` behavior:
  the same files land at the same paths.

Nothing here imports boto3 at module load — only :class:`S3ObjectStore` does, and
only when constructed — so ``local`` mode never needs the AWS SDK installed.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid a config<->objectstore import cycle at load time
    from .config import RagSettings


class ObjectStore(ABC):
    """Rooted key/value blob store. Keys are POSIX relative paths."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes | None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def iter_keys(self, prefix: str = "", *, suffix: str | None = None) -> Iterator[str]:
        """Yield keys under ``prefix`` (optionally filtered by ``suffix``), sorted
        for deterministic ordering across backends."""

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def signature(self, prefix: str = "") -> tuple[int, float]:
        """Cheap change signature ``(count, latest_marker)`` for cache
        invalidation — ``latest_marker`` is a modification epoch (local mtime / S3
        LastModified) or a monotonic version (in-memory)."""

    @property
    @abstractmethod
    def location(self) -> str:
        """Stable identity string for this rooted store (a cache key across the
        fresh store instances the factories return per call)."""

    # ── text convenience (utf-8), shared by all backends ─────────────────────
    def put_text(self, key: str, text: str) -> None:
        self.put_bytes(key, text.encode("utf-8"))

    def get_text(self, key: str) -> str | None:
        data = self.get_bytes(key)
        return None if data is None else data.decode("utf-8")


def _norm(key: str) -> str:
    return key.strip("/")


class LocalObjectStore(ObjectStore):
    """Filesystem-backed store rooted at a directory. Byte-identical to the prior
    direct-``pathlib`` writes/reads."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*_norm(key).split("/"))

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_bytes(self, key: str) -> bytes | None:
        p = self._path(key)
        try:
            return p.read_bytes()
        except OSError:
            return None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def iter_keys(self, prefix: str = "", *, suffix: str | None = None) -> Iterator[str]:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return
        keys = []
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            if suffix and not f.name.endswith(suffix):
                continue
            keys.append(f.relative_to(self.root).as_posix())
        yield from sorted(keys)

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except OSError:
            pass

    def signature(self, prefix: str = "") -> tuple[int, float]:
        base = self._path(prefix) if prefix else self.root
        if not base.exists():
            return (0, 0.0)
        files = [f for f in base.rglob("*") if f.is_file()]
        latest = max((f.stat().st_mtime for f in files), default=0.0)
        return (len(files), latest)

    @property
    def location(self) -> str:
        return f"local:{self.root}"


class InMemoryObjectStore(ObjectStore):
    """In-process fake for tests (no filesystem, no network). Mirrors the S3 key
    space so a suite can exercise the ``s3`` code path without moto/boto3."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._version = 0

    def put_bytes(self, key: str, data: bytes) -> None:
        self._data[_norm(key)] = bytes(data)
        self._version += 1

    def get_bytes(self, key: str) -> bytes | None:
        return self._data.get(_norm(key))

    def exists(self, key: str) -> bool:
        return _norm(key) in self._data

    def iter_keys(self, prefix: str = "", *, suffix: str | None = None) -> Iterator[str]:
        pre = _norm(prefix)
        for k in sorted(self._data):
            if pre and not (k == pre or k.startswith(pre + "/")):
                continue
            if suffix and not k.endswith(suffix):
                continue
            yield k

    def delete(self, key: str) -> None:
        if self._data.pop(_norm(key), None) is not None:
            self._version += 1

    def signature(self, prefix: str = "") -> tuple[int, float]:
        return (sum(1 for _ in self.iter_keys(prefix)), float(self._version))

    @property
    def location(self) -> str:
        return f"mem:{id(self)}"


class S3ObjectStore(ObjectStore):
    """S3-backed store rooted at ``s3://bucket/{prefix}``. Credentials come from the
    ambient AWS chain (EC2 instance profile / env) — never passed in here. boto3 is
    imported lazily so ``local`` mode never requires the SDK."""

    def __init__(self, bucket: str, *, prefix: str = "", region: str | None = None,
                 client: object | None = None):
        if not bucket:
            raise ValueError("S3ObjectStore requires a bucket name")
        self.bucket = bucket
        self.prefix = _norm(prefix)
        if client is not None:
            self.client = client
        else:  # pragma: no cover - needs boto3 + network
            import boto3
            self.client = boto3.client("s3", region_name=region)

    def _full(self, key: str) -> str:
        k = _norm(key)
        return f"{self.prefix}/{k}" if self.prefix else k

    def put_bytes(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._full(key), Body=data)

    def get_bytes(self, key: str) -> bytes | None:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=self._full(key))
        except Exception:  # NoSuchKey / ClientError -> treat as absent
            return None
        return resp["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._full(key))
            return True
        except Exception:
            return False

    def _list(self, prefix: str = "") -> Iterator[dict]:
        full_prefix = self._full(prefix) if prefix else (self.prefix + "/" if self.prefix else "")
        token: str | None = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": full_prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []) or []:
                yield obj
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")

    def _strip(self, full_key: str) -> str:
        if self.prefix and full_key.startswith(self.prefix + "/"):
            return full_key[len(self.prefix) + 1:]
        return full_key

    def iter_keys(self, prefix: str = "", *, suffix: str | None = None) -> Iterator[str]:
        keys = []
        for obj in self._list(prefix):
            key = self._strip(obj["Key"])
            if suffix and not key.endswith(suffix):
                continue
            keys.append(key)
        yield from sorted(keys)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._full(key))

    def signature(self, prefix: str = "") -> tuple[int, float]:
        count = 0
        latest = 0.0
        for obj in self._list(prefix):
            count += 1
            lm = obj.get("LastModified")
            if lm is not None:
                latest = max(latest, lm.timestamp())
        return (count, latest)

    @property
    def location(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"


# ── factory + settings helpers ───────────────────────────────────────────────

def build_object_store(*, backend: str, local_root: Path | str, s3_subprefix: str,
                       s3_bucket: str | None = None, s3_prefix: str = "corpus",
                       s3_region: str | None = None, client: object | None = None
                       ) -> ObjectStore:
    """Construct the right store for ``backend`` (``local`` | ``s3``).

    ``local_root`` is the on-disk directory used in local mode; ``s3_subprefix`` is
    the logical corpus sub-path (e.g. ``processed/ingest``) appended to ``s3_prefix``
    in S3 mode so the bucket layout mirrors the local ``data/`` tree."""
    if backend == "s3":
        prefix = "/".join(p for p in (_norm(s3_prefix), _norm(s3_subprefix)) if p)
        return S3ObjectStore(s3_bucket or "", prefix=prefix, region=s3_region, client=client)
    return LocalObjectStore(local_root)


def _resolve_storage_env() -> tuple[str, str | None, str, str | None]:
    """Deployment storage config from the environment — used by the ingest layer,
    which has its own config subsystem (no RagSettings). Mirrors the RAG-side
    ``_apply_env_overrides`` so both layers make the same local/s3 decision."""
    mode = os.environ.get("MACRORAG_DEPLOY_MODE", "local").strip().lower()
    backend = os.environ.get("MACRORAG_STORAGE_BACKEND") or ("s3" if mode == "aws" else "local")
    return (backend, os.environ.get("MACRORAG_S3_BUCKET"),
            os.environ.get("MACRORAG_S3_PREFIX", "corpus"), os.environ.get("MACRORAG_S3_REGION"))


def object_store_from_env(local_root: Path | str, s3_subprefix: str,
                          *, client: object | None = None) -> ObjectStore:
    backend, bucket, prefix, region = _resolve_storage_env()
    return build_object_store(backend=backend, local_root=local_root, s3_subprefix=s3_subprefix,
                              s3_bucket=bucket, s3_prefix=prefix, s3_region=region, client=client)


def _storage_params(settings: RagSettings) -> tuple[str, str | None, str, str | None]:
    """(backend, bucket, prefix, region) from a settings object. Tolerant of a
    minimal/duck-typed settings (test fakes exposing only path properties): a
    missing ``storage``/``storage_backend`` defaults to local."""
    backend = getattr(settings, "storage_backend", "local")
    storage = getattr(settings, "storage", None)
    return (backend, getattr(storage, "s3_bucket", None),
            getattr(storage, "s3_prefix", "corpus"), getattr(storage, "s3_region", None))


def corpus_store(settings: RagSettings, *, client: object | None = None) -> ObjectStore:
    """Object store for the processed ``ParsedDocument`` JSON corpus."""
    backend, bucket, prefix, region = _storage_params(settings)
    return build_object_store(
        backend=backend, local_root=settings.processed_path, s3_subprefix="processed/ingest",
        s3_bucket=bucket, s3_prefix=prefix, s3_region=region, client=client)


def manifest_store(settings: RagSettings, *, client: object | None = None) -> tuple[ObjectStore, str]:
    """(store, key) for the index manifest — a single object under ``vectorstore/``."""
    backend, bucket, prefix, region = _storage_params(settings)
    mpath = settings.manifest_path
    store = build_object_store(
        backend=backend, local_root=mpath.parent, s3_subprefix="vectorstore",
        s3_bucket=bucket, s3_prefix=prefix, s3_region=region, client=client)
    return store, mpath.name
