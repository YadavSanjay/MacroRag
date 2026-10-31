# rechunk-idempotent — rechunk leaves a correctly chunked corpus untouched

## Requirement

`scripts/rechunk_processed.py` (also run by the `POST /admin/curate` rechunk job) re-splits
stored chunks that are over the token budget. It exists for corpora chunked by an old splitter
that left oversized single chunks. It decides "oversized" with
`estimate_tokens(text) > max_tokens`.

The current splitter, `ingest.common.chunking._split_by_budget`, guarantees less than that: it
packs atoms into windows of at most `max_tokens` and then prefixes each window after the first
with an overlap tail of up to `overlap_tokens`. So a correctly produced chunk can be up to
`max_tokens + overlap_tokens` (with the committed settings, 1000 + 80 tokens, about 4,320
characters). On 2026-10-21, `rechunk_processed --dry-run` on the local corpus reported 100
documents to re-split and 751 chunks to add, while its largest chunk stayed the same size
(e.g. 4,053 → 4,053 characters): it was splitting in-budget chunks again, which would add
near-duplicate overlapping chunks, force re-embedding and change the corpus id.

When this change is done:

- A stored chunk is re-split only when it is longer than the splitter's own bound,
  `max_tokens + overlap_tokens` (estimated as `estimate_tokens` does). A chunk within that
  bound passes through unchanged, text and metadata.
- So a document chunked by the current `Chunker` is never changed, and a second run of the
  script changes nothing after a first run (idempotent).
- A genuinely oversized chunk (above the bound) is still split, as today, and every piece it
  becomes is within the bound.

## Acceptance criteria

- **C1**: For documents whose chunks were produced by the current
  `ingest.common.chunking.Chunker` with the committed chunking settings
  (`ingest.common.config.load_common_settings().chunking`), from any body text,
  `rechunk_processed.main(["--dry-run"], store=<store holding them>)` reports
  `docs changed: 0` and `rechunk_doc` returns each document's chunks unchanged (same count,
  texts and chunk ids). Oracle: Requirement (the splitter's documented bound).
- **C2**: A stored chunk whose `estimate_tokens` is above `max_tokens + overlap_tokens` is split
  by `rechunk_doc` into at least two chunks, each within that bound in estimated tokens (the
  tests keep paragraphs at most 3,998 characters, where that also means at most
  `(max_tokens + overlap_tokens) * chars_per_token` = 4,320 characters; the splitter's
  `"\n\n"` joiner can otherwise add 2 characters without adding an estimated token), with
  the section metadata kept and `seq` / `chunk_id` renumbered across the document; and running
  `rechunk_doc` again on the result changes nothing. Oracle: Requirement.

## Interface

No new functions. Exercised as is: `scripts/rechunk_processed.py` (`resplit`, `rechunk_doc`,
`main(argv, store=...)`), `ingest.common.chunking.Chunker`, `estimate_tokens`,
`ingest.common.config.load_common_settings`, `ingest.common.storage.DocumentStore`,
`ingest.common.models.ParsedDocument` / `Chunk`.

## Notes

- Committed chunking settings (`configs/ingest/common.yaml`): `max_tokens` 1000,
  `overlap_tokens` 80, `chars_per_token` 4, so the bound is 1,080 estimated tokens, 4,320
  characters.
- A store for the tests: a `DocumentStore` over a temporary directory or the in-memory object
  store, as the existing curate tests (`test/test_curate_objectstore.py`) build one. Synthetic
  text only; no network, no `data/`, no clock.
- `scripts/` is not a package: put it on `sys.path` and `import rechunk_processed`, as
  `test/test_curate_objectstore.py` does (it also builds a `DocumentStore` over an
  `InMemoryObjectStore`).
