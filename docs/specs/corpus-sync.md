# corpus-sync — copy the processed corpus from one object store to another

## Requirement

The AWS deployment (`deploy_mode: aws`) keeps the processed corpus in S3 and is meant to be
populated by running `ingest` on the instance (guide 09 §6.6). A fresh ingest cannot reproduce the
local corpus, which is the curated one:
- FRED serves the ICE BofA IG/HY OAS only over a rolling window of about three years. The stored
  per-year `fred_daily` documents are the only copy of years the window has passed (guide 02).
- Three FOMC minutes (December 2025, January and March 2026) have left the Fed feed. They were
  recovered locally by hand on 2026-10-15.
- The rollout so far (Fed retags, minutes backfill, ACM backfill, episode cards) would
  otherwise be repeated step by step on the instance.

The time-series store is rebuilt from the processed corpus on every `index`, and the vector store
and manifest are rebuilt by `index`. So copying the processed documents into the S3 store, then
running a full `index` there, reproduces the local corpus on AWS.

This spec adds the copy, written against the `ObjectStore` seam so it is testable with the
in-memory fake and works for any pair of stores.

Out of scope:
- deleting keys from the destination (a sync never removes data; the operator removes departed
  documents explicitly);
- the raw store, the ingest cache SQLite and the Qdrant collection;
- running `index` (the operator does it after the copy).

## Acceptance criteria

All criteria concern `utils.corpus_sync.sync_objects(src, dst, *, prefix="", dry_run=False,
overwrite=False) -> dict[str, int]`, which considers every key `src.iter_keys(prefix)` yields.
The returned dict has exactly the keys `copied`, `identical`, `conflict`, `overwritten`,
`dst_only`.

- **C1**: A source key missing from `dst` is written to `dst` under the same key, byte for byte,
  and counted in `copied`. A key present in both with equal bytes is not written and is counted
  in `identical`. Oracle: hand-built byte payloads in in-memory stores, compared exactly.
- **C2**: A key present in both with different bytes:
  - with `overwrite=False` (the default), is not written, so `dst` keeps its bytes, and is
    counted in `conflict`;
  - with `overwrite=True`, is written with the source bytes and counted in `overwritten`.

  `copied + identical + conflict + overwritten` equals the number of source keys considered.
  Oracle: hand-built payloads; the count identity is arithmetic.
- **C3**: Nothing is ever deleted from `dst`. Keys in `dst` under `prefix` that are not source
  keys keep their bytes and are counted in `dst_only`. `src` is never written. Oracle: store
  contents before and after.
- **C4**: `dry_run=True` writes nothing to `dst` and returns the same counts as the real run on
  the same stores. Oracle: write log of a counting fake store; the counts of the real run that
  follows.
- **C5**: With a non-empty `prefix`, only source keys under it are considered, and only `dst`
  keys under it count toward `dst_only`. Keys outside the prefix are neither written nor counted.
  A second sync with the same arguments reports `copied = 0` and `overwritten = 0` and writes
  nothing. When those arguments include `overwrite=True`, it also reports `conflict = 0`.
  Oracle: hand-built stores with keys inside and outside the prefix.

## Interface

- `utils.corpus_sync.sync_objects(src: ObjectStore, dst: ObjectStore, *, prefix: str = "",
  dry_run: bool = False, overwrite: bool = False) -> dict[str, int]`: new module and function.
- `scripts/sync_corpus.py [--apply] [--overwrite] [--prefix P]`: new thin wrapper, not under
  spec test. The source is the local processed directory (`LocalObjectStore`). The destination is
  the S3 processed store from the AWS storage settings (`MACRORAG_S3_*` env or the config). It is
  a dry run unless `--apply` is given, and prints the counts.

## Notes

- Fixtures: `utils.objectstore.InMemoryObjectStore` (the S3 fake), with a subclass that records
  writes and deletes. No network, no `data/`, no clock.
- Comparing bytes needs one read of every destination key that is also a source key. That is
  about 4,000 GETs for the current corpus, which is acceptable for a one-off seed.
- AWS rollout (for the implementation PR description and guide 09 §6.6):
  1. `python scripts/sync_corpus.py` (dry run) against the bucket, then `--apply`;
  2. on the instance, `python -m pipelines.cli index` (full build: vectors, time series,
     manifest);
  3. check `/api/healthz` `built: true` and that the `world_view_id` digest matches the local
     one. The digest is a pure function of `(chunk_id, content_hash)`, so equal corpora give the
     same id.
