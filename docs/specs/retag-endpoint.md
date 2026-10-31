# retag-endpoint — re-apply an endpoint's configured tags to its stored documents

## Requirement

Chunk tags are stamped at ingest from the endpoint's `factor_tags` / `entity_tags` in its tier
config. Changing the config affects only future ingests: documents already in the processed store
keep the old tags, and no tool rewrites them.

This matters now: the Fed endpoints `press_monetary` and `speeches` carry no `inflation` tag.
When a question such as "What is the outlook for US inflation and the Fed policy path?" gets the
automatic `factor_tags=["inflation"]` filter, every Fed chunk is excluded. 11–14 of the 15–19 chunks
returned are then from the BoJ outlook report (2026-10-15).
- Reranked without the filter, Fed speeches outscore the BoJ chunks for that question: 0.30 vs
  0.25, and 11 of the top 15.
- FOMC statements and Fed speeches routinely discuss inflation, growth and the labour market.

This spec adds a tool that re-applies configured tags to stored documents, and it fixes the two
Fed endpoints' config. A full re-index afterwards carries the new tags into the vector store.

Out of scope:
- re-indexing (the operator runs `index`; tags are not in the embedded text, so embeddings are
  unchanged);
- making `index --incremental` detect tag-only changes;
- the missing FOMC minutes and SEP text (a separate change).

## Acceptance criteria

- **C1**: `ingest.common.retag.retag_documents(store, *, tier, source_id, factor_tags,
  entity_tags, dry_run=False) -> dict[str, int]`:
  - covers every key under `f"tier{tier}/{source_id}/"` ending `.json` in the
    `utils.objectstore.ObjectStore`;
  - sets every chunk's `metadata.factor_tags` and `metadata.entity_tags` to the given lists (in
    the given order);
  - a document whose chunks all already carry those lists is not written;
  - a changed document is written back as `ParsedDocument.model_dump_json(indent=2,
    exclude_none=True)`, the processed doc store's format.

  Parsed, a written document equals the original in every field except the chunk tags: same
  `doc_id`, `content_hash`, chunk ids, texts and other metadata.
  Returns `{"docs": <keys read>, "changed_docs": <documents with at least one changed chunk>,
  "changed_chunks": <chunks whose tags changed>}`. Oracle: hand-built documents in an in-memory
  store, compared field by field.
- **C2**:
  - `dry_run=True` returns the same counts and writes nothing.
  - A second run with the same tags reports `changed_docs = 0` and `changed_chunks = 0`, and
    writes nothing.
  - Keys outside the prefix (another source, another tier, a non-`.json` key under the prefix)
    are neither read nor written.

  Oracle: the store's contents compared before and after.
- **C3**: in `configs/ingest/tier1_fed.yaml`, the `press_monetary` and `speeches` endpoints'
  `factor_tags` include `inflation`, `growth` and `labor`, and keep their existing tags; every
  tag is in `configs/ingest/vocabulary.yaml`. Oracle: this list.

## Interface

- `ingest.common.retag.retag_documents(store: ObjectStore, *, tier: int, source_id: str,
  factor_tags: list[str], entity_tags: list[str], dry_run: bool = False) -> dict[str, int]` —
  new module and function.
- `scripts/retag_endpoint.py --tier <tier config stem> --endpoints <ids,...> [--dry-run]` — new
  thin wrapper: loads the tier config and calls `retag_documents` for each endpoint, with the
  processed store from `object_store_from_env`. Not under spec test.

## Notes

- Fixtures: `utils.objectstore.InMemoryObjectStore`, documents built with
  `ingest.common.models.ParsedDocument`. No network, no `data/`, no clock.
- Rollout (for the implementation PR description):
  1. `python scripts/retag_endpoint.py --tier tier1_fed --endpoints press_monetary,speeches --dry-run`;
  2. run it without `--dry-run`;
  3. `python -m pipelines.cli index` (full rebuild, about 80 min on CPU);
  4. rerun the US-inflation retrieval probe.
  On AWS the same script runs against S3 through the object store.
- The `world_view_id` digest covers `(chunk_id, content_hash)` only, so a retag does not change
  it. The rebuild still changes retrieval. This is a known limit of the digest; not changed here.
