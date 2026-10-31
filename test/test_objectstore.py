"""ObjectStore abstraction (dual-mode local/S3) — the seam that lets corpus object
data live on the local filesystem or in S3 per ``deploy_mode``.

No network / no boto3: the S3 backend is exercised with an injected in-memory fake
client that mimics the boto3 S3 surface (put/get/head/list/delete)."""

from __future__ import annotations

import json

import pytest

from utils.objectstore import (
    InMemoryObjectStore,
    LocalObjectStore,
    S3ObjectStore,
    build_object_store,
    object_store_from_env,
)


# ── a minimal in-memory stand-in for a boto3 S3 client ───────────────────────
class _FakeS3Client:
    """Implements just the calls S3ObjectStore uses, over an in-memory dict."""

    class _Body:
        def __init__(self, data): self._d = data
        def read(self): return self._d

    def __init__(self):
        self.store: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body):
        self.store[Key] = bytes(Body)

    def get_object(self, *, Bucket, Key):
        if Key not in self.store:
            raise KeyError("NoSuchKey")
        return {"Body": self._Body(self.store[Key])}

    def head_object(self, *, Bucket, Key):
        if Key not in self.store:
            raise KeyError("404")
        return {}

    def delete_object(self, *, Bucket, Key):
        self.store.pop(Key, None)

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for k in self.store if k.startswith(Prefix))
        # exercise pagination: one key per page
        start = int(ContinuationToken) if ContinuationToken else 0
        page = keys[start:start + 1]
        contents = [{"Key": k} for k in page]
        truncated = start + 1 < len(keys)
        resp = {"Contents": contents, "IsTruncated": truncated}
        if truncated:
            resp["NextContinuationToken"] = str(start + 1)
        return resp


# ── shared contract, run against every backend ───────────────────────────────
def _local(tmp_path): return LocalObjectStore(tmp_path / "root")
def _mem(_): return InMemoryObjectStore()
def _s3(_): return S3ObjectStore("bkt", prefix="corpus/processed", client=_FakeS3Client())

BACKENDS = [_local, _mem, _s3]


@pytest.mark.parametrize("make", BACKENDS)
def test_put_get_roundtrip(make, tmp_path):
    s = make(tmp_path)
    s.put_text("tier1/src/a.json", '{"x": 1}')
    s.put_bytes("tier1/src/b.bin", b"\x00\x01")
    assert s.get_text("tier1/src/a.json") == '{"x": 1}'
    assert s.get_bytes("tier1/src/b.bin") == b"\x00\x01"
    assert s.get_text("missing") is None
    assert s.get_bytes("missing") is None


@pytest.mark.parametrize("make", BACKENDS)
def test_exists_and_delete(make, tmp_path):
    s = make(tmp_path)
    assert not s.exists("k.json")
    s.put_text("k.json", "v")
    assert s.exists("k.json")
    s.delete("k.json")
    assert not s.exists("k.json")
    s.delete("k.json")  # idempotent, no raise


@pytest.mark.parametrize("make", BACKENDS)
def test_iter_keys_sorted_and_suffix_filtered(make, tmp_path):
    s = make(tmp_path)
    for k in ("tier2/z.json", "tier1/a.json", "tier1/b.txt", "tier1/c.json"):
        s.put_text(k, "x")
    assert list(s.iter_keys(suffix=".json")) == [
        "tier1/a.json", "tier1/c.json", "tier2/z.json"]
    assert list(s.iter_keys(prefix="tier1", suffix=".json")) == [
        "tier1/a.json", "tier1/c.json"]


@pytest.mark.parametrize("make", BACKENDS)
def test_leading_slash_normalized(make, tmp_path):
    s = make(tmp_path)
    s.put_text("/k.json", "v")
    assert s.get_text("k.json") == "v"
    assert s.exists("/k.json")


@pytest.mark.parametrize("make", BACKENDS)
def test_signature_changes_on_write(make, tmp_path):
    s = make(tmp_path)
    sig0 = s.signature()
    assert sig0[0] == 0
    s.put_text("a.json", "1")
    assert s.signature()[0] == 1
    s.put_text("b.json", "2")
    assert s.signature()[0] == 2


def test_s3_key_prefixing_and_stripping():
    client = _FakeS3Client()
    s = S3ObjectStore("bkt", prefix="corpus/processed", client=client)
    s.put_text("tier1/a.json", "v")
    # stored under the full prefixed key ...
    assert "corpus/processed/tier1/a.json" in client.store
    # ... but iter_keys yields the logical (stripped) key
    assert list(s.iter_keys()) == ["tier1/a.json"]
    assert s.location == "s3://bkt/corpus/processed"


def test_s3_pagination_collects_all_keys():
    client = _FakeS3Client()
    s = S3ObjectStore("bkt", prefix="p", client=client)
    for i in range(5):
        s.put_text(f"k{i}.json", "v")
    assert list(s.iter_keys(suffix=".json")) == [f"k{i}.json" for i in range(5)]


# ── factory + env resolution ─────────────────────────────────────────────────
def test_build_object_store_local(tmp_path):
    s = build_object_store(backend="local", local_root=tmp_path, s3_subprefix="processed/ingest")
    assert isinstance(s, LocalObjectStore)


def test_build_object_store_s3_composes_prefix():
    s = build_object_store(backend="s3", local_root="unused", s3_subprefix="processed/ingest",
                           s3_bucket="bkt", s3_prefix="corpus", client=_FakeS3Client())
    assert isinstance(s, S3ObjectStore)
    assert s.location == "s3://bkt/corpus/processed/ingest"


def test_env_resolution_defaults_local(monkeypatch, tmp_path):
    for v in ("MACRORAG_DEPLOY_MODE", "MACRORAG_STORAGE_BACKEND", "MACRORAG_S3_BUCKET"):
        monkeypatch.delenv(v, raising=False)
    s = object_store_from_env(tmp_path, "raw/ingest")
    assert isinstance(s, LocalObjectStore)


def test_env_resolution_aws_selects_s3(monkeypatch, tmp_path):
    monkeypatch.setenv("MACRORAG_DEPLOY_MODE", "aws")
    monkeypatch.setenv("MACRORAG_S3_BUCKET", "bkt")
    monkeypatch.setenv("MACRORAG_S3_PREFIX", "corpus")
    s = object_store_from_env(tmp_path, "raw/ingest", client=_FakeS3Client())
    assert isinstance(s, S3ObjectStore)
    assert s.location == "s3://bkt/corpus/raw/ingest"


# ── corpus + manifest round-trips through the abstraction ────────────────────
def test_iter_documents_over_object_store():
    from utils.corpus import iter_documents
    store = InMemoryObjectStore()
    store.put_text("tier1/a.json", json.dumps({"doc_id": "a", "tier": 1}))
    store.put_text("tier1/b.json", json.dumps({"doc_id": "b", "tier": 1}))
    store.put_text("tier1/note.txt", "ignored")  # non-json skipped
    docs = list(iter_documents(store))
    assert [d["doc_id"] for d in docs] == ["a", "b"]


def test_iter_documents_back_compat_with_path(corpus_dir):
    """A plain directory path still works (wrapped in a LocalObjectStore)."""
    from utils.corpus import iter_documents
    docs = list(iter_documents(corpus_dir))
    assert len(docs) == 4  # the conftest fixture writes 4 docs


def test_manifest_roundtrip_via_store():
    from utils.manifest import read_manifest, write_manifest
    store = InMemoryObjectStore()
    write_manifest((store, "manifest.json"), {"pipeline_version": "1.0.0"})
    assert store.exists("manifest.json")
    assert read_manifest((store, "manifest.json"))["pipeline_version"] == "1.0.0"
    assert read_manifest((InMemoryObjectStore(), "manifest.json")) is None


def test_manifest_roundtrip_via_path_is_byte_identical(tmp_path):
    """Path-based manifest write lands the same file at the same place as before."""
    from utils.manifest import read_manifest, write_manifest
    p = tmp_path / "vectorstore" / "manifest.json"
    write_manifest(p, {"pipeline_version": "1.0.0", "chunks": 3})
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["chunks"] == 3
    assert read_manifest(p)["chunks"] == 3


# ── config: deploy_mode + storage backend derivation ─────────────────────────
def test_deploy_mode_default_and_backend_derivation():
    from utils.config import RagSettings
    assert RagSettings().deploy_mode == "local"
    assert RagSettings().storage_backend == "local"
    assert RagSettings(deploy_mode="aws").storage_backend == "s3"          # derived
    # an explicit backend wins over the deploy_mode default
    assert RagSettings(deploy_mode="aws", storage={"backend": "local"}).storage_backend == "local"


def test_env_overrides_populate_storage(monkeypatch):
    from utils.config import _apply_env_overrides
    monkeypatch.setenv("MACRORAG_DEPLOY_MODE", "aws")
    monkeypatch.setenv("MACRORAG_S3_BUCKET", "my-bucket")
    monkeypatch.setenv("MACRORAG_S3_PREFIX", "corp")
    cfg = _apply_env_overrides({})
    assert cfg["deploy_mode"] == "aws"
    assert cfg["storage"] == {"s3_bucket": "my-bucket", "s3_prefix": "corp"}


# ── ingest writers still land local files byte-identically in default mode ───
def test_rawstore_writes_local_in_default_mode(monkeypatch, tmp_path):
    for v in ("MACRORAG_DEPLOY_MODE", "MACRORAG_STORAGE_BACKEND", "MACRORAG_S3_BUCKET"):
        monkeypatch.delenv(v, raising=False)
    from ingest.common.config import StorageSettings
    from ingest.common.storage import RawStore
    st = StorageSettings(data_dir=str(tmp_path))
    key = RawStore(st).save(1, "src", "file.bin", b"hi")
    assert key == "tier1/src/file.bin"
    assert (st.raw_dir / "tier1" / "src" / "file.bin").read_bytes() == b"hi"
