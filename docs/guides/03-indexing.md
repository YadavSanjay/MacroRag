# 03 — Indexing (build the vector DB + time-series store)

Indexing is **stage 2**: it reads the parsed documents from
`data/processed/ingest/`, embeds the narrative chunks into **Qdrant**, loads the
numeric rows into the **SQLite time-series store**, and writes a **manifest** that
stamps the corpus snapshot (`world_view_id`).

> Audience: technical operators. Prereq: you have run [02 – Data Ingestion](02-data-ingestion.md).
> Design background: [pipeline design](../design/Macro_RAG_pipeline_design.md).

---

## 1. The index command

```
python -m pipelines.cli index [--incremental [--dry-run]] [--append]
```

| Flag | Default | Meaning |
|---|---|---|
| _(none)_ | — | **Full rebuild** — recreate the Qdrant collection from scratch and re-embed the whole corpus |
| `--incremental` | off | **True incremental** — embed/upsert only **new + changed** chunks and delete departed ones; the unchanged majority is left untouched (no full re-embed) |
| `--dry-run` | off | With `--incremental`: report the plan (new/changed/unchanged/stale counts) **without writing** anything or loading the embedder |
| `--append` | off | Re-embed the whole corpus but **keep** the existing collection (no drop). Rarely needed — prefer `--incremental` |

On completion it prints, e.g.:

```
indexed: 5234 chunks, 1810 observations
```

What it does, in order:
1. Reads every `ParsedDocument` JSON under `data/processed/ingest/`.
2. Embeds chunks with `BAAI/bge-m3` (dense 1024-d + sparse) in batches and upserts to
   the Qdrant collection `macro_chunks`.
3. Rebuilds the time-series store from the numeric rows.
4. Writes `data/vectorstore/manifest.json` (counts + `world_view_id`) and appends the build
   to `manifest_history.jsonl` (§4).

> **Embedding runs on the shared RagInference service by default** — indexing and retrieval
> both resolve their embedder through `embeddings.select_embedder`, so the service must be
> reachable for a build (or set `RAGINFER_URL=""` to load bge-m3 in-process). See
> [01 §4.5](01-setup-and-prerequisites.md).

Outputs:

| Artifact | Path |
|---|---|
| Vector store | the Qdrant server's collection `macro_chunks` (embedded mode: `data/vectorstore/qdrant/`) |
| Time-series store | `data/vectorstore/timeseries.sqlite` |
| Index manifest | `data/vectorstore/manifest.json` |

> Under `deploy_mode: aws` the processed corpus is read from **S3** and the **manifest** is
> written to S3 (via `utils.objectstore`); the time-series SQLite stays on the local/EBS
> filesystem and vectors go to the external Qdrant server. Paths above are the `local` layout.
> See [08 §2.1](08-operations-and-deployment.md) + [AWS deployment](../design/Macro_RAG_AWS_Deployment.md).

---

## 2. Full vs incremental indexing

### Full rebuild (default)

```bash
python -m pipelines.cli index
```

Use this when: it's the first build, you changed the embedder/chunking, you ran a
maintenance script (§5), or you suspect the index drifted from the corpus. It drops
and recreates the Qdrant collection and re-embeds every chunk, so it's the safe "make
the index match `data/processed/` exactly" option (~80 min on CPU for a full corpus).

### Incremental refresh (recommended after an ingest)

```bash
python -m pipelines.cli index --incremental --dry-run   # preview the plan
python -m pipelines.cli index --incremental             # apply it
```

The **true incremental** path: it embeds/upserts only the chunks that are **new or
changed** and deletes chunks that have **left** the corpus — the unchanged majority is
never re-embedded. New/changed is decided by diffing each processed chunk's
`content_hash` against what the store already holds (`existing_signatures()` scrolls the
collection); departed chunks are found by set difference and removed. Use it after an
**incremental ingest** ([02 §4](02-data-ingestion.md)).

The same refresh runs as an admin job, without a shell on the host: `POST /admin/index`
with `{"incremental": true}` (add `"dry_run": true` for the plan only), or the **Refresh
changed** / **Preview refresh** buttons on the UI's Index page. The job's result is the stats
shown below, and a running refresh can be cancelled between embedding batches (admin
endpoint table in [guide 05](05-api-and-integration.md)).

`--dry-run` reports the plan without writing or even loading the embedder, e.g.:

```
DRY-RUN (nothing written): scanned=3287 new=290 changed=501 unchanged=2496 stale=109
```

A real run then prints what it wrote:

```
incremental refresh: scanned=3287 new=290 changed=501 unchanged=2496 stale=109
  embedded/upserted=791 deleted=109 | total chunks=3287 observations=31189
```

It is **idempotent** — a second `--incremental` run right after reports
`new=0 changed=0 stale=0` (nothing to do), which also makes `--dry-run` a quick
"is the index in sync with `data/processed/`?" check.

### Append (full re-embed, keeps the collection)

```bash
python -m pipelines.cli index --append
```

`--append` skips the collection **drop** but still re-embeds the **entire** corpus
(every processed chunk is re-embedded and upserted). It does not skip unchanged chunks —
that's what `--incremental` is for. Prefer `--incremental`; reach for `--append` only if
you specifically want to overwrite-in-place without a drop.

> **Note on the time-series store:** every mode (full / incremental / append) resets and
> reloads the time-series SQLite from the processed corpus, so the numeric store always
> reflects the full current `data/processed/ingest/`. The flags only control the
> *vector* collection's recreate-vs-diff-vs-upsert behaviour.

### Typical incremental cycle

```bash
python -m ingest --tier tier1_fed --refresh   # 1. pull the latest docs (content-dedup skips unchanged)
python -m pipelines.cli index --incremental    # 2. embed only new+changed, drop departed
python -m pipelines.cli eval                    # 3. (optional) confirm no retrieval regression
```

The same cycle applies after a `fred_daily` / `market_episodes` ingest
([02 §4](02-data-ingestion.md#fred-daily-series-and-market-episode-cards)). The FRED series are
numeric only and reach the time-series store on any index run. The episode cards are vector
chunks, so synthesis cannot cite them until `index --incremental` has embedded them. Adding or
changing cards changes the corpus, so `world_view_id` changes with it.

---

## 3. Where the data goes (vector store modes)

The same `index` command targets whichever Qdrant you've configured in
`configs/rag.yaml`:

```yaml
qdrant:
  url: http://localhost:6333   # server mode — what configs/rag.yaml ships
  collection: macro_chunks
  quantization: false         # set true for int8 (~4x smaller, ~1% recall cost)
  # embedded on-disk alternative (small single-process runs only — it deadlocks on bulk upsert):
  # url: null
  # path: data/vectorstore/qdrant
```

Start the Qdrant server before indexing:

```bash
docker compose -f infra/docker/docker-compose.yml up -d qdrant
```

> **One-writer rule for embedded mode:** the on-disk store can only be opened by one
> process at a time. Don't run `index` while a `serve` process is holding the same
> `data/vectorstore/qdrant`. Stop the server first, or use server mode. Over the API,
> `/admin/index` (and `/admin/ingest`, `/admin/curate`) are guarded by an exclusive job
> lock that returns **HTTP 409 (JobConflict)** on a concurrent run
> ([Production Hardening §H](../design/Macro_RAG_Production_Hardening.md)).

---

## 4. The manifest & `world_view_id`

`data/vectorstore/manifest.json` records the build's pipeline version, embedding
model/dimension/grounding setting, reranker, LLM tiers, a hash of the whole prompt
registry, the collection name, chunk/observation counts — and a `world_view_id`.

**The id is content-addressed:** `macro-<YYYY-MM-DD>-<12-hex digest>`, where the digest is
taken over the sorted `(chunk_id, content_hash)` pairs of the whole corpus
(`utils.manifest.corpus_digest`). Two consequences worth relying on:

- re-indexing an **unchanged** corpus produces the **same** id, so an unchanged rebuild is
  visibly a no-op rather than looking like a new world view;
- two **different** corpora built on the same day cannot collide on one id.

Manifests written before digests existed keep their legacy date-only form — they are not
retroactively given an identity they never had.

**Every build appends to `manifest_history.jsonl`** (beside the manifest, in the same object
store). Two admin endpoints read it ([05 §4](05-api-and-integration.md)):

| Endpoint | Answers |
|---|---|
| `GET /admin/manifest/history` | What builds happened, each with a field-level diff against the previous one |
| `GET /admin/manifest/verify?world_view=…` | For an old shock vector: *was this ever our corpus, and is it still the live one?* |

This is an audit **trail, not a snapshot store**. `world_view_id` remains a
**provenance/freshness stamp**: re-indexing *replaces* the live corpus, so you still cannot
retrieve or re-synthesize against a superseded id. That is deliberate — see
[05 §5](05-api-and-integration.md) for the canonical definition and
[Production Hardening §F](../design/Macro_RAG_Production_Hardening.md) for the rationale and what an
immutable snapshot store would take.

---

## 5. Maintenance scripts (re-process without re-downloading)

These operate on already-downloaded `data/processed/ingest/` JSON. After running any
of them, **re-index** (`python -m pipelines.cli index`) so the stores pick up the
changes. All are idempotent and most support `--dry-run`.

| Script | What it does | Run |
|---|---|---|
| `scripts/normalize_vocab.py` | Canonicalize `factor_tags`/`entity_tags` against `vocabulary.yaml` (alias map), keep chunk `doc_type` in sync, reclassify `unknown` doc types | `python scripts/normalize_vocab.py [--dry-run]` |
| `scripts/rechunk_processed.py` | Re-split stored chunk text with the current budget splitter; renumber `seq`/`chunk_id` (preserves section metadata) | `python scripts/rechunk_processed.py [--dry-run]` |
| `scripts/reextract_dfast.py` | Re-extract DFAST supervisory-scenario numerics from CSVs into clean long-format time series; strip legacy grid rows | `python scripts/reextract_dfast.py [--strip-only]` |
| `scripts/reextract_sep.py` | Re-extract SEP projections from the Fed projections HTML into clean long rows | `python scripts/reextract_sep.py [--dry-run]` |
| `scripts/retag_endpoint.py` | Re-apply endpoints' configured `factor_tags`/`entity_tags` (tier config) to their stored chunks, after a tag change in the config. Changes nothing else; local or S3 | `python scripts/retag_endpoint.py --tier tier1_fed --endpoints press_monetary,speeches [--dry-run]` |

> Tags are not part of the embedded text, and `index --incremental` compares text hashes, so it
> does **not** see a tag-only change: run a full `index` after `retag_endpoint.py`.

> These are also exposed as **curate** jobs over the API
> (`POST /admin/curate` with `task: normalize-vocab|rechunk|reextract-sep|reextract-dfast`),
> see [05](05-api-and-integration.md).

---

## 6. Verifying the index

```bash
# retrieval-only query (no LLM needed) — confirms chunks are searchable
python -m pipelines.cli query "DFAST severely adverse scenario" --no-llm

# health/manifest via the API
python -m pipelines.cli serve &
curl -s http://127.0.0.1:8000/healthz | jq
# admin store stats (vector count, time-series sources)
curl -s http://127.0.0.1:8000/admin/stores/vector | jq
curl -s http://127.0.0.1:8000/admin/stores/timeseries | jq
```

---

## 7. Troubleshooting (indexing)

| Symptom | Likely cause | Fix |
|---|---|---|
| `index` reports `0 chunks` | `data/processed/ingest/` empty | Run ingestion first ([02](02-data-ingestion.md)); confirm JSON files exist |
| `index` fails on connection error / `ImportError` about torch | RagInference unreachable; indexing embeds through it ([01 §4.5](01-setup-and-prerequisites.md)) | Start the service (`curl :8799/healthz`), fix `RAGINFER_URL`, or set `RAGINFER_URL=""` to embed in-process |
| Qdrant "storage already accessed by another instance" | A `serve`/other process holds the embedded store | Stop it, or switch to Qdrant **server** mode (`qdrant.url`) |
| Re-index didn't reflect a fixed/edited doc | Its `content_hash` didn't change (e.g. only chunk metadata edited by hand) | Run a **full** `index` (no flag) to recreate the collection, or verify the doc's `content_hash` actually changed |
| Query returns stale/old chunks after re-ingest | Index not refreshed | Run `index --incremental` (or a full `index`); check `manifest.json` counts changed. `index --incremental --dry-run` shows if anything is pending |
| Embedding very slow / OOM | CPU-only, large batch | Use GPU; lower `embedding.batch_size`; enable `qdrant.quantization` |
| Vector dimension mismatch on upsert | Changed embedder/dense_dim against an old collection | Full rebuild (recreate) so the collection matches the new vectors |
| Time-series queries empty but PDFs ingested | Numeric rows weren't extracted | Run the relevant re-extract script (§5), then re-index |
| Tag filters return nothing despite matching docs | Tags not normalized to canonical vocab | Run `normalize_vocab.py`, then re-index |

Next: ask questions and tune retrieval → [04 – Query, Retrieval & Eval](04-query-retrieval-eval.md).
