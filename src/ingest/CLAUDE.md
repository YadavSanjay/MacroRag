# src/ingest — tiered source ingestion

Turns each external macro source into a `ParsedDocument` (narrative chunks + numeric
series) written to `data/processed/ingest/` (or the S3 corpus prefix under
`deploy_mode: aws` — writes go through `utils.objectstore` via `RawStore`/`DocumentStore`),
which the index pipeline then embeds.
Design/rationale: [docs/design/Macro_RAG_Ingestion_Design.md](../../docs/design/Macro_RAG_Ingestion_Design.md)
and [Macro_RAG_Source_Catalog.md](../../docs/design/Macro_RAG_Source_Catalog.md) (the code +
`configs/ingest/tier*.yaml` are the source of truth when they disagree).

## Fixed package shape (follow it exactly)

Each source family is a package `src/ingest/<tier>/` (fed, treasury, usstats, intl, house,
power, chips, aimacro, scenarios) with:
- `ingestor.py` — `class <Tier>Ingestor(TierIngestor)`: discovers items + dispatches handlers.
- `common_base.py` — `class <Tier>Handler(BaseSourceHandler)`: shared fetch/parse for the family.
- `parsers.py` — family-specific parsing helpers.
- `handlers/<source>.py` — `class <Source>Handler(<Tier>Handler)`: one per source.

Class hierarchy (in `common/`): `EndpointHandler` (abc, `base.py`) → `BaseSourceHandler`
(`handler_base.py`, adds HTTP/render/cache plumbing) → `<Tier>Handler` → `<Source>Handler`.
`TierIngestor` (`base.py`) is the per-tier driver. A handler implements
`fetch(item: DiscoveredItem) -> list[ParsedDocument]`.

## Shared infra (`common/`)

- `models.py` — `DiscoveredItem`, `FetchResult`, `Chunk`, `ChunkMetadata`, `ParsedDocument`
  (the ingestion data model — don't invent parallel types).
- `handler_base.py` — `_fetch_or_render` (polite HTTP with `_render` Playwright **fallback**,
  not a hard requirement), ETag/If-Modified-Since caching.
- `dateextract.py` — `extract_date_from_{text,html,pdf}`. **All narrative handlers are dated
  corpus-wide**; derive `release_date` here rather than leaving chunks undated.
- `chunking.py`, `pdf/html_extract`, `sdmx`, `spreadsheet`, `http_client`, `cache`, `storage`,
  `browser`, `config`, `feeds`, `federal_register`.

## Adding a source

1. Add its endpoint(s) to `configs/ingest/<tier>.yaml` (the catalog — canonical).
2. Create `handlers/<source>.py` subclassing the tier handler; implement discovery +
   `fetch` → `ParsedDocument`(s). Reuse `common/` parsers/dating; emit rich `ChunkMetadata`
   (publisher, publication, `release_date`, tier, bucket, doc_type, section, `factor_tags`,
   `entity_tags`). Numeric-only sources emit `timeseries` instead of chunks.
3. Register it with the tier ingestor; add a fake-fixture test (no network).

## CLI

`python -m ingest --tier <tier> [--endpoints ...] [--limit N] [--refresh] [--list]`
(`__main__.py` / `cli.py`). Tiers: `tier1_fed … tier7_scenarios`.

## Conventions

- **No network in tests** — feed handlers canned HTML/PDF/JSON fixtures; assert the parsed
  chunks/series + derived dates. Mirror the existing `test/test_*_dating.py` / ingest tests.
- Respect `User-Agent` + rate limiting + conditional GET (already in `http_client`/`handler_base`);
  don't hand-roll fetches.
- Controlled vocab (`factor_tags`/`entity_tags`) comes from `configs/ingest/vocabulary.yaml` —
  tag against it, don't free-text. The vocabulary is now editable at runtime via
  `PUT /admin/vocabulary` → `common.config.save_vocabulary_fields` (persists to the module
  `VOCAB_PATH` and invalidates the cache), so treat `vocabulary.yaml` as the seed, not the
  only writer (Production Hardening §E).
