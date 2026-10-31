# 02 — Data Ingestion (download & parse sources)

Ingestion is **stage 1** of the pipeline: it fetches source material (PDFs, HTML, RSS,
spreadsheets, REST APIs) and parses it into `ParsedDocument` JSON under
`data/processed/ingest/`, ready to be indexed ([03](03-indexing.md)).

This guide covers the `python -m ingest` CLI, **full download from scratch**,
**incremental download** of just-the-latest data, the tier system, the API keys each
tier needs, and troubleshooting.

> Audience: technical operators. Design background:
> [Ingestion design](../design/Macro_RAG_Ingestion_Design.md) ·
> [Source catalog](../design/Macro_RAG_Source_Catalog.md).

---

## 1. The ingest CLI

```
python -m ingest [--tier TIER] [--endpoints a,b,c] [--limit N] [--refresh] [--list] [--log-level LVL]
```

| Flag | Default | Meaning |
|---|---|---|
| `--tier` | `tier1_fed` | Which tier config (stem) under `configs/ingest/` to run |
| `--endpoints` | all enabled | **Comma-separated** endpoint IDs to run (subset of the tier) |
| `--limit` | none | Max **new** documents to fetch per endpoint (great for smoke tests) |
| `--refresh` | off | Bypass the discovery cache and re-list everything available (still dedups already-parsed content) |
| `--list` | off | Print the tier's endpoints (id, bucket, cadence, release schedule, discovery URL) and exit |
| `--log-level` | from `LOG_LEVEL` | Override logging verbosity for this run |

**Exit codes:** `0` success · `1` errors occurred *and* nothing was parsed · `2` unknown tier.

The run prints a report summarizing discovered / fetched / parsed / skipped / errored
counts.

---

## 2. The tiers

Each tier is one config file `configs/ingest/<tier>.yaml`. Pass the **stem** to `--tier`.

> There are **seven numbered tiers but nine config files** — Tier 6 is split into `6a`
> (power), `6b` (chips) and `6c` (ai-macro), because they are different source families on
> different cadences. `--tier` always takes a config stem, so nine values are valid.

| `--tier` value | Tier | Source family | Example content | API keys |
|---|---|---|---|---|
| `tier1_fed` | 1 | Federal Reserve | FOMC statements/minutes, SEP, DFAST stress tests, speeches | none |
| `tier2_treasury` | 2 | U.S. Treasury | TIC, quarterly refunding, outbound-investment rules | none |
| `tier3_usstats` | 3 | U.S. statistical agencies | BEA NIPA, BLS employment/CPI, CBO outlook, FRED daily market series, market-episode cards ([§4](#fred-daily-series-and-market-episode-cards)) | `BEA_API_KEY`, `BLS_API_KEY`, `FRED_API_KEY` |
| `tier4_intl` | 4 | International institutions | IMF WEO/GFSR, BIS quarterly/annual, BoJ | none |
| `tier5_house` | 5 | Sell-side house views | Apollo, Goldman, Morgan Stanley, JPM macro views | none (some JS-rendered → Playwright) |
| `tier6a_power` | 6a | Power & data-center electricity | EIA AEO/STEO, IEA Energy&AI, FERC, PJM (FERC-714 forward-demand and PJM binding-constraint signals are not extracted — [Production Hardening §C](../design/Macro_RAG_Production_Hardening.md)) | `EIA_API_KEY`, `IEA_ACCOUNT`/`IEA_PASSWORD`, `PJM_API_KEY` (opt) |
| `tier6b_chips` | 6b | Semiconductors & export controls | Federal Register rules, BIS entity list, TSMC/SIA | none |
| `tier6c_aimacro` | 6c | AI-macro / productivity / geopolitics | GPR & EPU indices, Census BTOS, Stanford AI Index | `CENSUS_API_KEY` |
| `tier7_scenarios` | 7 | Cross-asset stress & geopolitical scenarios | EBA/ESRB adverse scenario, IMF GFSR geopolitical chapter, Taiwan-conflict studies | none |

Inspect any tier before running it:

```bash
python -m ingest --tier tier6a_power --list
```

### Endpoints, buckets and cadence

A tier contains **endpoints** (one per source feed). Each endpoint has a `bucket`
that signals how time-sensitive it is — **A** = catalyst/event (cutoff-sensitive),
**B** = periodic release, **C** = rolling commentary. Buckets later drive time-decay
ranking ([04](04-query-retrieval-eval.md)) and `--endpoints` lets you target a subset.

---

## 3. Full download from scratch

A "from scratch" download is just a normal run with an **empty cache**. The cache
(`data/cache/ingest_cache.sqlite`) holds HTTP validators (ETag / Last-Modified) and a
ledger of already-parsed documents. With nothing cached, every discovery feed is
fetched and every document is parsed.

```bash
# Option A — first ever run: cache is already empty, just run each tier
python -m ingest --tier tier1_fed
python -m ingest --tier tier2_treasury
python -m ingest --tier tier3_usstats
python -m ingest --tier tier4_intl
python -m ingest --tier tier5_house
python -m ingest --tier tier6a_power
python -m ingest --tier tier6b_chips
python -m ingest --tier tier6c_aimacro
python -m ingest --tier tier7_scenarios

# Option B — force a clean re-download later: delete the cache first
rm data/cache/ingest_cache.sqlite       # PowerShell: Remove-Item data\cache\ingest_cache.sqlite
python -m ingest --tier tier1_fed
```

> The path is `data/cache/…`, not a top-level `cache/` — it resolves under
> `storage.data_dir` (`data/`) plus `storage.cache_db` (`cache/ingest_cache.sqlite`)
> in `configs/ingest/common.yaml`.

Notes:
- Raw bytes land in `data/raw/ingest/<tier>/<source>/…` (kept verbatim for audit/replay).
- Parsed output lands in `data/processed/ingest/<tier>/<source>/<doc>.json`.
- Tiers are independent — run only the ones you have keys/need for.
- `--limit N` caps documents per endpoint, ideal for a first smoke test:
  ```bash
  python -m ingest --tier tier1_fed --limit 5
  ```

> Even a "full" run only fetches what discovery currently lists (e.g. the documents a
> feed/index page exposes today). It is not a deep historical backfill of every
> document ever published unless the source's index exposes them.

---

## 4. Incremental download (get just the latest)

This is the **normal, routine** mode — run the same command again later and only new
or changed material is processed. Two mechanisms make it cheap and idempotent:

1. **Conditional GET at discovery.** The client stores each feed's `ETag` /
   `Last-Modified`. On the next run it sends `If-None-Match` / `If-Modified-Since`; a
   `304 Not Modified` means the feed is unchanged and discovery is skipped entirely
   (logged as `discovery unchanged (304)`).
2. **Content-hash ledger.** Before parsing, each document's body is SHA-256 hashed and
   checked against the `seen_documents` ledger. Already-parsed content is skipped
   (`already processed, skipping`).

So routine incremental ingestion is simply:

```bash
# run the same tier again — only new/changed docs are fetched and parsed
python -m ingest --tier tier1_fed
```

### When the feed cache is stale but you still don't want to re-parse everything

Use `--refresh`. It **bypasses the discovery cache** (re-lists everything currently
available, ignoring the 304 short-circuit) but the content-hash ledger still dedups
documents you've already parsed. This is the "catch-up after the cache drifted" mode:

```bash
python -m ingest --tier tier6c_aimacro --refresh
```

| Goal | Command |
|---|---|
| Routine "get the latest" | `python -m ingest --tier <tier>` |
| Re-discover but don't re-parse seen docs | `python -m ingest --tier <tier> --refresh` |
| Full clean re-download (re-fetch + re-parse all) | delete `data/cache/ingest_cache.sqlite`, then run |
| One endpoint only, latest few | `python -m ingest --tier <tier> --endpoints <id> --limit 3` |

### Scheduling incremental runs

Ingestion is designed for "on-demand / weekly" cadence. To automate it, wrap the
per-tier commands in your scheduler of choice (cron, Task Scheduler, or the
`/admin/ingest` job API in [05](05-api-and-integration.md)). Match the cadence to each
tier's bucket — A-bucket sources (FOMC, Federal Register) reward daily/weekly polling;
C-bucket commentary can be slower.

### FRED daily series and market-episode cards

Three `tier3_usstats` endpoints supply the 10-day evidence for short-horizon shock vectors
(specs `ingest-fred-daily`, `ingest-acm-daily` and `market-episodes`). Endpoint ids, series and episode list:
`configs/ingest/tier3_usstats.yaml`.

- **`fred_daily`** fetches 13 daily FRED series (VIX, UST 2/5/10/30, ICE BofA IG/HY OAS,
  Baa−10Y, 10Y real yield, Kim–Wright term premium, SOFR, USD/TWD, USD/JPY). It is numeric
  only: one document per series per calendar year, loaded into the time-series store, with no
  vector chunks. Needs `FRED_API_KEY`.
- **`acm_daily`** downloads the NY Fed's ACM term-premium workbook (`ACMTermPremium.xls`, sheet
  `ACM Daily`) and stores the ACM 10Y term premium (`ACMTP10`) the same way, one numeric
  document per year. ACM is the model faultline uses; the Kim–Wright series above is a
  different model. No key needed. A routine run rewrites the current and previous year;
  `--refresh` backfills from 2005.
- **`market_episodes`** builds one card per named stress episode: flight-to-quality rallies (Lehman
  2008 to the April 2025 tariff shock) and Treasury sell-offs (the 2013 taper tantrum, the June
  2022 CPI shock, the August 2023 refunding), from the rows `fred_daily` stored. It makes no HTTP
  request, so it must run after `fred_daily`; running the whole tier does that.
  Each card gives the realized move over
  the 5 and 10 **trading** days after the anchor close, in shock-field units, or over the
  episode's own `horizons` when it sets them (e.g. `deepseek` adds 1 day). The chunks carry
  `doc_type: market_episode`, publisher `MacroRAG (derived from FRED)`, an exact `horizon_days`,
  and a `release_date` at the end of the card's longest measurement window.

```bash
python -m ingest --tier tier3_usstats --endpoints fred_daily,market_episodes   # routine
python -m ingest --tier tier3_usstats --endpoints fred_daily --refresh         # backfill from 2005
```

A routine run fetches only the current and previous year. `--refresh` backfills from
`backfill_start` (2005-01-01), but writes an old year only when FRED also returned a row
before it, which proves the window did not cut into that year.

> **The stored per-year documents are the only copy of older spread history.** FRED serves the
> ICE BofA OAS pair only over a rolling window of about three years (the stored history starts
> 2023-10-16). Once the window moves past a year, FRED cannot give it back. Never
> rebuild `data/processed/ingest/tier3/fred_daily/` by deleting and re-fetching. Before the
> window, the cards use Baa−10Y for IG and label it as a yield spread, not an OAS.

The cards are new vector chunks, so follow the ingest with an incremental index
([03 §2](03-indexing.md)).

### Re-fetching a document, and items that have left a feed

Two caches stop a plain re-ingest from replacing a stored document: the processed ledger
(`seen_documents`) and the HTTP validators (`fetch_cache` — an unchanged page answers
`304 Not Modified`). To force a re-parse, remove the processed document, delete its URL from
**both** tables, then re-ingest and index:

```bash
sqlite3 data/cache/ingest_cache.sqlite \
  "DELETE FROM seen_documents WHERE source_id='<endpoint>' AND doc_url IN (<urls>);
   DELETE FROM fetch_cache WHERE url IN (<urls>);"
python -m ingest --tier <tier> --endpoints <endpoint> --refresh
python -m pipelines.cli index --incremental
```

**Feeds are rolling windows** (the FOMC feed lists about 15 recent items), so an item that has
left its feed is not re-discovered. It can still be fetched from its URL: build the
`DiscoveredItem` the feed would have given (for FOMC minutes: `doc_type="fomc_minutes"`,
`chunking="sections"`, the title and release date), run the handler's `fetch`, then
`doc_store.save` and `cache.mark_processed`, as the ingest loop does. FOMC minutes items store
the minutes page itself, sectioned by `section_headings`, not the press release that links
it. Because some stored documents can no longer be re-fetched this way or at all (feed items,
the rolling FRED window above), treat the processed corpus as data to back up and to seed new
environments from ([09 §6.6](09-packaging-and-deployment.md)), not as a cache.

---

## 5. What a parsed document looks like

Each `data/processed/ingest/.../<doc>.json` is a `ParsedDocument` with provenance,
narrative `chunks` (for the vector store) and optional numeric `timeseries` (for the
SQLite store). Every chunk carries metadata used later as retrieval facets:
`publisher`, `publication`, `release_date`, `doc_type`, `tier`, `bucket`,
`entity_tags`, `factor_tags`, `trades_affected`, and `prev/next_chunk_id` for
neighbour expansion.

The **controlled vocabulary** (valid `factor_tags`, `entity_tags`, `doc_types`, and
alias normalization such as `AI → ai`, `ai_investment → ai_capex`) lives in
`configs/ingest/vocabulary.yaml`. If you change it, re-normalize existing data with
`scripts/normalize_vocab.py` ([03 §5](03-indexing.md)).

---

## 6. Formats & handlers (what can be ingested)

| Format | Library | Notes |
|---|---|---|
| PDF (text) | `pypdf` | Fast text extraction (born-digital PDFs) |
| PDF (tables) | `pdfplumber` | Table-aware (DFAST/SEP/FSR). Optional — skipped if absent |
| HTML / RSS | `beautifulsoup4` + `lxml` + `feedparser` + `trafilatura` | CSS/XPath body selectors, main-content fallback |
| REST JSON | `httpx` | BEA/BLS/Census/EIA APIs |
| Spreadsheets | `openpyxl` (.xlsx) / `xlrd` (.xls) | GPR (.xls), EPU (.xlsx) → time-series rows |
| JS-rendered pages | `playwright` (Chromium) | Fallback when polite HTTP hits a bot wall |
| Pinned documents | (PDF/HTML above) | Tier 7 `pinned_docs` handler — stable per-document URLs with an authoritative `release_date`, no feed to scrape |

Politeness/robustness defaults live in `configs/ingest/common.yaml`: per-host rate
limit (1 req/s default), timeouts, retry/backoff on 429/5xx, and a descriptive
User-Agent whose contact address comes from `MACRORAG_CONTACT_EMAIL` in `.env`
([01 §3.1](01-setup-and-prerequisites.md)).

---

## 7. Troubleshooting (ingestion)

| Symptom | Likely cause | Fix |
|---|---|---|
| Run reports `0 parsed` but no errors | Everything already cached/seen (incremental no-op) | Expected. Use `--refresh` to re-discover, or delete the cache for a full re-download |
| Endpoint logs "missing API key, skipping" | Required key not in `.env` | Add the key (see tier table §2); re-run that endpoint with `--endpoints <id>` |
| HTTP 403 / bot wall on a source | Site blocks polite HTTP / needs JS render | Ensure `playwright install chromium`; tier configs already fall back to browser render |
| HTTP 429 / rate limited | Polling too fast | Built-in backoff handles transient 429s; lower `rate_limit_per_sec` in `common.yaml` if persistent |
| Stale data — newest release not picked up | Discovery cache returned 304 | Run with `--refresh` to bypass the discovery cache |
| "Deleted the cache" but ingestion still skips everything | Removed a top-level `cache/` that doesn't exist | The real path is `data/cache/ingest_cache.sqlite` (§3) |
| PDF parsed but tables missing | `pdfplumber` not installed | `pip install pdfplumber` (it's in requirements); re-run |
| Spreadsheet (.xls) fails | `xlrd` missing or modern .xlsx mislabelled | Ensure `xlrd` (legacy .xls) and `openpyxl` (.xlsx) installed |
| `unknown tier 'X'` (exit code 2) | Wrong `--tier` stem | Use a valid stem (see §2); list with `--tier <t> --list` |
| Login-gated source returns nothing (IEA/USITC) | Missing/expired account env vars | Set `IEA_ACCOUNT`/`IEA_PASSWORD`; verify the account still works |
| Want to inspect what was actually fetched | — | Raw bytes are preserved under `data/raw/ingest/…`; open them directly |

Next: turn `data/processed/ingest/` into searchable stores → [03 – Indexing](03-indexing.md).
