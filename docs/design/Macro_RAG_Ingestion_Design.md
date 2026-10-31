# MacroRAG — Ingestion Design

How MacroRAG turns external macro sources into citable chunks and exact numeric series:
the shared ingestion architecture, the retrieval-strategy buckets, and a per-endpoint
description of every source family (Tiers 1–7).

**Authority.** The endpoint list lives in `configs/ingest/tier*.yaml`; handler behaviour lives
in `src/ingest/*/handlers/`. This doc explains the design and the parsing hazards behind them.
*Which* sources and *why* — and how they cover faultline's book — is the
[Source Catalog](Macro_RAG_Source_Catalog.md). Operating the CLI is
[guide 02](../guides/02-data-ingestion.md); the package shape for adding a source is
[src/ingest/CLAUDE.md](../../src/ingest/CLAUDE.md).

Section numbers are stable: handler docstrings and tier configs cite them (e.g. "§4.1").

---

## 1. Architecture

```
configs/ingest/tier*.yaml ──► TierIngestor ──► <Source>Handler
                                 │  Stage A: discover (feed / index page / API / URL probe)
                                 │  Stage B: fetch → persist raw bytes → parse
                                 ▼
                         ParsedDocument  (narrative Chunks + numeric timeseries rows)
                                 │
            RawStore / DocumentStore  (utils.objectstore: local FS, or S3 in aws mode)
                                 │
         index pipeline ──► Qdrant (hybrid dense+sparse)   +   SQLite time-series store
```

- **Two stores, deliberately split.** Prose goes to the vector index for retrieval; exact
  numbers (NIPA tables, BLS series, FRED daily, DFAST paths, SDMX flows, index downloads) go
  to the time-series store so an answer can quote the figure rather than an embedding's
  paraphrase of it. Many sources emit both from one fetch.
- **Package shape.** One package per source family (`fed`, `treasury`, `usstats`, `intl`,
  `house`, `power`, `chips`, `aimacro`, `scenarios`), each with `ingestor.py`,
  `common_base.py`, `parsers.py` and `handlers/<source>.py`. Shared infrastructure is in
  `src/ingest/common/` (models, HTTP, cache, chunking, PDF/HTML extraction, dating, SDMX,
  spreadsheets, Federal Register client, headless browser).
- **Storage.** Raw bytes land under `raw/ingest/<tier>/<source>/` before parsing; parsed
  documents under `processed/ingest/<tier>/<source>/`. Both go through `utils.objectstore`, so
  `deploy_mode: aws` writes the same layout to S3. The conditional-GET cache
  (`cache/ingest_cache.sqlite`: url, etag, last_modified, hash, fetched_at) stays local.
- **PDF extraction is text + tables only** (`common/pdf_extract.py`). Numbers that exist only
  inside chart images are not recovered; where a source publishes the chart data separately
  (FSR accessible tables, DFAST CSVs, SEP tables), the structured file is ingested instead.
  See [Production Hardening §C](Macro_RAG_Production_Hardening.md#c--ingestion-depth-not-built).
- **Dating.** Every narrative chunk carries a `release_date`, derived through
  `common/dateextract.py` (`extract_date_from_{text,html,pdf}`) when the source does not give
  one. The citation gate refuses undated evidence, so an undated chunk is useless to synthesis.
- **Chunking** (`common/chunking.py`, `configs/ingest/common.yaml`): section-aware first
  (handler-supplied headings), then a token budget (`max_tokens: 1000`, `overlap_tokens: 80`).
  Short documents (FOMC statements, press notices) stay whole. Chunking is deterministic; a
  change to it needs a full re-index (see [guide 03](../guides/03-indexing.md)).
- **Controlled vocabulary.** `factor_tags` / `entity_tags` are normalized against
  `configs/ingest/vocabulary.yaml` at config load; the vocabulary is editable at runtime via
  `PUT /admin/vocabulary`.

---

## 2. Endpoint inventory

Discovery URLs as configured. "TS" = time-series store, "VS" = vector store.

### 2.1 Credentials

All free. Secrets come from `.env` or a `<NAME>_FILE` mount (`ingest.common.config.get_secret`).
Handlers that embed a key in the request (BEA, BLS, EIA, FRED) persist a redacted descriptor
URL (`bea://…`, `bls://…`, `eia://…`, `fred://SERIES/YEAR`) so the key never reaches stored data.

| Service | Variable | Used by |
|---|---|---|
| FRED | `FRED_API_KEY` | `fred_daily` |
| BEA | `BEA_API_KEY` | `bea` |
| BLS | `BLS_API_KEY` | `bls` (without a key: 25 queries/day) |
| EIA | `EIA_API_KEY` | `eia_aeo`, `eia_steo` |
| Census | `CENSUS_API_KEY` | `census_btos` |
| IEA | `IEA_*` (account) | `iea` (PDFs may be account-gated) |
| PJM | `Ocp-Apim-Subscription-Key` | `pjm` (not provisioned; handler is a no-op without it) |

### 2.2 Tier 1 — Federal Reserve (`tier1_fed`)

| Endpoint | Discovery | Output |
|---|---|---|
| `press_monetary` | RSS `federalreserve.gov/feeds/press_monetary.xml` | VS (statements, minutes, MPR) + TS (SEP tables) |
| `financial_stability` | HTML index `/publications/financial-stability-report.htm` | VS + TS (chart tables) |
| `sloos` | HTML index `/data/sloos.htm` (`sloos-YYYYMM.htm` pages) | VS (+ data-file links) |
| `dfast` | HTML hub `/supervisionreg/stress-tests-capital-planning.htm` → per-year page | VS (scenario narrative) + TS (scenario and historic CSVs) |
| `speeches` | RSS `/feeds/speeches_and_testimony.xml` | VS |

### 2.3 Tier 2 — U.S. Treasury (`tier2_treasury`)

| Endpoint | Discovery | Output |
|---|---|---|
| `quarterly_refunding` | HTML "Most Recent Quarterly Refunding Documents" page | VS (policy statement, TBAC report/minutes) + TS (auction/buyback XML) |
| `oisp` | Federal Register API (Office of Investment Security) + program-regulations page + FAQ PDF | VS |
| `tic` | Predictable URL `Press-notice-TIC-for-{MMM}-{YYYY}.pdf` + landing page | VS (press notice) + data ZIP persisted |

### 2.4 Tier 3 — U.S. statistics and market series (`tier3_usstats`)

| Endpoint | Discovery | Output |
|---|---|---|
| `bea` | REST `apps.bea.gov/api/data/` (curated NIPA tables) | TS |
| `bls` | REST POST `api.bls.gov/publicAPI/v2/timeseries/data/` | TS |
| `cbo` | HTML index `cbo.gov/data/publications-with-data-files` | VS (+ data-file links) |
| `fred_daily` | REST `api.stlouisfed.org/fred/series/observations` | TS (one document per series per year) |
| `acm_daily` | Download `newyorkfed.org/.../ACMTermPremium.xls` | TS (one document per series per year) |
| `market_episodes` | None — computed from stored `fred_daily` / `acm_daily` rows | VS (episode cards) |

### 2.5 Tier 4 — International (`tier4_intl`)

| Endpoint | Discovery | Output |
|---|---|---|
| `imf_sdmx` | IMF SDMX 3.0 API `api.imf.org/external/sdmx/3.0` (curated dataflow keys) | TS |
| `imf_reports` | Predictable media paths for WEO and GFSR components | VS |
| `bis_quarterly` | Predictable `bis.org/publications/qr-{YYYYMM}.pdf` + issue-page article scrape | VS |
| `bis_annual` | Predictable `bis.org/publications/aer-{YYYY}.pdf` | VS |
| `boj_outlook` | Predictable `boj.or.jp/en/mopo/outlook/gor{YYMM}a.pdf` | VS + TS (projection tables) |

### 2.6 Tier 5 — House views (`tier5_house`)

| Endpoint | Discovery | Output |
|---|---|---|
| `jpm_eotm` | HTML hub (Eye on the Market) → `{slug}-amv.pdf` | VS |
| `blackrock` | Configured direct URLs (weekly commentary, outlook) | VS |
| `gs_tom` | Headless render of `goldmansachs.com/insights/top-of-mind` | VS |
| `ms_totm` | HTML index of *Thoughts on the Market* episodes (transcripts) | VS |
| `apollo` | WordPress REST `apolloacademy.com/wp-json/wp/v2/posts` | VS |

### 2.7 Tier 6 — AI-specific (`tier6a_power`, `tier6b_chips`, `tier6c_aimacro`)

| Endpoint | Discovery | Output |
|---|---|---|
| `eia_aeo`, `eia_steo` | EIA v2 REST (`/aeo/{year}`, `/steo`) | TS |
| `iea` | HTML scrape of `iea.org/reports/energy-and-ai` for blob PDFs | VS |
| `ferc_714` | HTML index → annual ZIP | Raw ZIP + index document |
| `pjm` | PJM Data Miner 2 API | TS (when a key is provisioned) |
| `federal_register` | Federal Register API (two queries, §9B.1) | VS |
| `bis_entity_list` | HTML landing page | VS snapshot (+ export links) |
| `sia` | HTML archive of press releases | VS + headline numbers |
| `tsmc` | `pr.tsmc.com/english/latest-news` (HTTP, headless fallback) | VS + headline numbers |
| `gpr`, `epu` | Spreadsheet downloads (`index_download`) | TS |
| `census_btos` | Census EITS API `api.census.gov/data/timeseries/eits/btos` | TS |
| `stanford_hai` | Predictable `ai_index_report_{YYYY}.pdf` (HEAD-probed newest first) | VS |

Tier 7 (`tier7_scenarios`, six pinned documents) is §10.

### 2.8 Operational conventions

Implemented in `common/http_client.py` and `common/handler_base.py`, configured in
`configs/ingest/common.yaml`:

- **Identify yourself.** Every request sends a descriptive `User-Agent`; the Fed, IEA and SIA
  silently rate-limit anonymous agents.
- **Rate limit.** 1 request/second/host by default; tier configs may tighten it. Retries with
  exponential backoff on 429/5xx (`max_retries: 3`), honouring `Retry-After`.
- **Conditional GET.** ETag / If-Modified-Since plus a body hash, so an unchanged discovery
  page costs one cheap request.
- **Raw bytes first.** Stage B persists the raw response before parsing, so a parser fix can
  re-run without re-fetching.
- **Headless browser as fallback only.** `_fetch_or_render` tries polite HTTP first and renders
  with Playwright only when the response is an empty JS shell or blocked (GS Top of Mind,
  TSMC). If both fail the handler degrades to a no-op rather than failing the tier.
- **Provenance on every chunk:** publisher, publication, `release_date`, retrieval date, URL,
  ETag, `content_hash`, licence, tier, bucket, `doc_type`, section, factor/entity tags.
  Licence values in use: `public_domain_us_gov`, `official_free_attribution`,
  `free_account_required`, `public_research_free`, `public_with_attribution_required`,
  `internal_analytical_use`.
- **Cadence** is declared per endpoint (`poll_cadence`); the ingest CLI is run by the operator
  or a scheduler — there is no in-process release-calendar monitor.

---

## 3. Retrieval strategy: why retrieve, and the buckets

### 3.1 Why retrieve content the model may already know

1. **Numeric precision.** A model that has seen an FOMC statement still confabulates the exact
   target range or dot-plot median. Shock magnitudes need the published figure.
2. **Traceability.** Every shock field must cite a dated chunk; "the model knew" is not
   auditable.
3. **Freshness.** FOMC meets every six weeks, data prints monthly, export-control rules land
   any day; there is always a window newer than the model's training data, and stress
   triggers are by nature recent.
4. **Determinism.** Output bound to specific chunk ids is reproducible; parametric recall is not.

Purely conceptual questions ("what is TBAC", "how does DFAST work") are not a corpus concern
— no definitional or methodology material is ingested. There is no parametric-only route in
the router; such a question simply retrieves little and the relevance floor
(`retrieval.min_rerank_score`) decides whether to answer.

### 3.2 Buckets

Every endpoint is assigned a bucket, stamped on each chunk (`bucket` payload field). Buckets
drive retrieval-time decay (`retriever/ranking.py`: catalysts and periodic reports age
differently from rolling commentary) and can be filtered on. Each endpoint's history window is
set by its `max_*` discovery limits in config.

| Bucket | Meaning | Window | Endpoints |
|---|---|---|---|
| **A — catalyst** | Policy/rule/event text whose exact wording can trigger a scenario | Deep recent history, cumulative | `press_monetary`, `speeches`, `quarterly_refunding`, `oisp`, `federal_register`, `bis_entity_list` |
| **B — periodic report** | Authoritative edition that supersedes the prior one | Latest edition, plus one or two prior for comparison | `financial_stability`, `dfast`, `cbo`, `bis_quarterly`, `bis_annual`, `imf_reports`, `boj_outlook`, `eia_aeo`, `eia_steo`, `iea`, `ferc_714`, `stanford_hai`, all Tier 7 |
| **C — rolling print/commentary** | Recurring stream where recency dominates but the trend matters | Rolling window | `sloos`, `tic`, `bea`, `bls`, `fred_daily`, `acm_daily`, `market_episodes`, `imf_sdmx`, all Tier 5, `pjm`, `tsmc`, `sia`, `gpr`, `epu`, `census_btos` |

---

## 4. Tier 1 — Federal Reserve

### 4.0 The two-stage pattern

Every endpoint has a **discovery** layer (feed, index page, API listing or URL probe) that
changes often and is cheap to poll, and **content** URLs that are immutable once published.
Stage A diffs the discovery layer against the seen set and enqueues new items; Stage B fetches
each item once, persists raw bytes, parses and chunks. Every later tier follows this shape.

### 4.1 `press_monetary` — FOMC statements, minutes, SEP, MPR

- **Feed:** RSS 2.0, ~15 items over a rolling ~6 months. The **title is the routing key**;
  `doc_type` comes from a regex table in config (statement, SEP/projections, minutes,
  discount-rate minutes, longer-run-goals reaffirmation). Release URLs are
  `…/pressreleases/monetary{YYYYMMDD}{a|b|c}.htm`, the suffix giving order within the day.
- **Statements** stay one chunk. Extracted fields: target range (the most important number),
  voters, dissenters with their preferred action, the forward-guidance paragraph. The sibling
  Implementation Note carries IORB / ON RRP rates the statement omits.
- **Minutes** are sub-chunked on their stable headings (financial developments, staff review,
  staff outlook, participants' views, committee policy action). Participants' views carry the
  forward-looking substance.
- **SEP:** projection tables (median, central tendency, range for GDP, unemployment, PCE, core
  PCE, fed funds) go to the TS via `sep_projections`; the explanatory text to the VS.
- Discount-rate minutes are low signal; they are ingested with low priority.

### 4.2 `financial_stability` — Financial Stability Report

- No feed; the landing page is a flat chronological index of editions back to 2018. PDF URLs
  are `…/files/financial-stability-report-{YYYYMMDD}.pdf` (older editions vary — treat URLs as
  opaque). Semi-annual (May/Nov); bucket B keeps the latest couple of editions.
- Chunked on the stable structure: executive summary, asset valuations, business/household
  borrowing, financial-sector leverage, funding risks, and the near-term-risks survey (the
  highest-signal section).
- The "Chart Data and Descriptions" accessible tables carry the numbers behind every chart and
  go to the TS — this is how chart content is recovered without image extraction.

### 4.3 `sloos` — Senior Loan Officer Opinion Survey

- Discovery scrapes `/data/sloos.htm` for `sloos-YYYYMM.htm` release pages, newest first. The
  `feeds/sloos.xml` RSS feed carries only Data Download Program notices, never the survey.
- A release is a bundle: the qualitative report (VS, sub-chunked on loan categories — C&I,
  CRE, residential, consumer — and the special-questions section) plus numeric charts data
  (CSV/XLSX), surfaced as links in `fields` for the time-series loader. Quarterly.

### 4.4 `dfast` — supervisory stress test

- Two-hop discovery: the landing page is a hub; artifacts live on a per-year page
  (`dfa-stress-tests-{YYYY}.htm`). Anchor text is mostly "PDF", so classification keys off the
  file URL. Bucket B: latest year, preferring the final vintage.
- **The companion CSVs are the source of truth** for numbers — one file per scenario × domain
  (baseline, severely adverse, and the historic series) — and go to the TS. The dense PDF
  tables are not parsed. The `historic` series (1976-Q1 onward, 28 variables) is what the
  realized-episode backtest scores against.
- The scenarios PDF contributes the narrative and methodology chunks.

### 4.5 `speeches` — speeches and testimony

- Same RSS schema as §4.1, 5–15 items a month with uneven signal. A priority filter parses
  speaker, role and venue from the title: Chair/Vice Chair and monetary-policy-titled speeches
  are priority 1, other voting Governors priority 2, ceremonial/community speeches priority 3
  (low retrieval weight).
- Long speeches are sub-chunked on paragraph clusters (they rarely carry headings).
- Expect silence in the FOMC blackout period; it is not a broken feed.

### 4.6 Fed HTML hazards

1. **Navigation chrome is ~95% of the served HTML.** Body extraction targets the article
   container; the breadcrumb at the top and the `Last Update:` line at the bottom are reliable
   boundaries when selectors fail.
2. **Prefer HTML over the PDF mirror** (`monetary{YYYYMMDD}a.htm` → `…a1.pdf`) unless the
   document is PDF-only (FSR, MPR, SEP, DFAST).
3. **Cloudflare email obfuscation** (`/cdn-cgi/l/email-protection#…`) is boilerplate; never
   decode it.
4. **No reliable `Last-Modified`** on release pages — the body-hash check is what detects change.

---

## 5. Tier 2 — U.S. Treasury

### 5.1 `quarterly_refunding` — Quarterly Refunding + TBAC

- A refunding is a **bundle released in waves over about three days** (Feb/May/Aug/Nov):
  financing estimates on the Monday; policy statement, TBAC report, TBAC minutes, recommended
  financing table, discussion charts, and auction/buyback schedules on the Wednesday.
- Stage A scrapes the "Most Recent Documents" page and classifies each artifact by anchor text
  via config `doc_type` rules. All artifacts share one `refunding_quarter`, so retrieval sees
  one logical event rather than fragments.
- Auction and buyback schedules are taken as **XML** (lossless; the PDF mirror is ignored) and
  go to the TS. The TBAC Report is the highest-value narrative and is sub-chunked on its
  numbered "Charge" sections.
- The headline press statement uses a sequential `sb{NNNN}` id that cannot be derived from
  the date — it must be discovered.

### 5.2 `oisp` — Outbound Investment Security Program

- Event-driven, no calendar. Three watchers: the Federal Register API (Office of Investment
  Security rules — the high-value items), the program-regulations page (standalone guidance
  PDFs), and the FAQ PDF (revised in place; re-ingested on body-hash change).
- Rules are taken from the API's rendered `body_html_url` rather than the PDF, and each chunk
  is stamped with effective date, covered sectors and countries of concern.
- Context: EO 14105 (2023), final rule 31 CFR Part 850 (effective 2025-01-02), and the COINS
  Act (FY2026 NDAA), which expands covered technologies and countries and requires new
  implementing regulations — a dated future catalyst.

### 5.3 `tic` — Treasury International Capital

- The monthly press notice sits at a predictable URL,
  `/system/files/136/Press-notice-TIC-for-{MMM}-{YYYY}.pdf`, where the month is the **release**
  month; the data lag is about six weeks. Stage A constructs and HEAD-probes recent months.
- The press notice (data period, net TIC flow, private/official split, next release date) is
  one chunk. The structured ZIP (incl. the Major Foreign Holders table) is located on the
  landing page and persisted for the time-series loader.

### 5.4 Treasury hazards

1. **Sequential press-release ids** (`sb{NNNN}`) — discovery only, never construction.
2. **Wave-released bundles** — group by `refunding_quarter`, not by artifact.
3. **XML over PDF** for schedules (the opposite of the Fed's HTML-over-PDF rule).
4. **Embargo timing** — TIC typically 4:00 PM ET; probing earlier returns 404.
5. **TIC archive format drift:** TXT (space/tab-delimited) throughout, a CSV added Apr–Sep 2023,
   replaced by HTML files from Sep 2023; the file-descriptions PDF in each ZIP identifies which
   file is which. The Major Foreign Holders series breaks at February 2023.
6. **Page banners** (e.g. the 2026 anniversary countdown) are boilerplate to strip.

---

## 6. Tier 3 — U.S. statistics and market series

Mostly clean REST APIs, so the work is **series curation** — which tables and series carry the
signal — kept in config, not code. Most output goes to the TS.

### 6.1 `bea` — Bureau of Economic Analysis

- GET `apps.bea.gov/api/data/` with `UserID`, dataset `NIPA`. Curated tables: `T20804` and
  `T20807` (PCE price index and its percent change — the Fed's preferred gauge), `T10101` (real
  GDP growth), `T10105` (nominal GDP), `T20306` (real PCE by type), `T50305` (private fixed
  investment by type — information-processing equipment and software corroborate the AI-capex
  cycle), `T61600D` (corporate profits by industry).
- `DataValue` carries thousands separators (`"27,719.4"`) — strip before casting. Periods come
  as `YYYY`, `YYYYQn`, `YYYYMnn`.

### 6.2 `bls` — Bureau of Labor Statistics

- **POST** with a JSON body; the registration key goes in the body. Series are batched (API max
  50 per request). Curated series: headline/NSA/core CPI, payrolls, average hourly earnings,
  unemployment, participation, employment-population ratio, JOLTS openings and quits, PPI
  final demand.
- Responses are newest-first; missing observations are `""` — null, never zero. Period codes:
  `M01–M12`, `Q01–Q04`, `A01`, `S01–S03`, paired with `year`.

### 6.3 `cbo` — Congressional Budget Office

- No API or feed; Stage A scrapes `publications-with-data-files` for `/publication/{ID}` pages.
- Stage B prefers the separate Executive Summary PDF over the 180+ page report, sub-chunks on
  the Outlook's chapter headings, and surfaces supplemental XLSX/CSV (the 10-year projections,
  including the 10-year Treasury yield path — the key term-premium prior) for the
  time-series loader.
- Currently yields no documents; see the Source Catalog's known gaps.

### 6.4 Cross-references

The Federal Register client is shared with Tier 6B (§9B.1) and OISP (§5.2). EIA forecasts are
§9A. SEC filings and CFTC/DTCC swap data are not ingested: company fundamentals belong to
XbrlRag, and live market data to faultline.

### 6.5 REST API hazards

1. **GET vs POST** — BEA is GET with query parameters, BLS is POST with a JSON body; the shared
   client supports both.
2. **Number formats** — BEA thousands separators; BLS empty-string nulls.
3. **Period encodings differ** — everything is normalized to sortable ISO dates at ingest.
4. **No release date in the payload** — BEA and BLS return "latest available"; the retrieval
   date is recorded and the publication date derived separately.
5. **A wrong series id fails silently or returns the wrong series** — another reason the
   curated lists live in reviewed config.

### 6.6 `fred_daily` — daily market series

- One request per configured series to FRED `series/observations`; rows split into **one
  document per series per calendar year** under a redacted `fred://SERIES/YEAR` descriptor.
- Series: `VIXCLS`, `BAMLC0A0CM` and `BAMLH0A0HYM2` (ICE BofA IG/HY OAS), `BAA10Y` (IG
  stand-in), `DGS2/5/10/30`, `DFII10` (10Y TIPS real yield), `THREEFYTP10` (Kim–Wright term
  premium), `SOFR`, `DEXTAUS`, `DEXJPUS`.
- **FRED serves the ICE BofA spreads only over a rolling ~3-year window** (from 2023-10-16 at
  the time of writing). A fetch must never replace stored history with a shorter one: routine
  runs fetch only the current and previous year, and a backfill (`--refresh`) writes an old
  year only when the response contains a row before it. The stored per-year documents are the
  only copy of older OAS history.

### 6.7 `acm_daily` — NY Fed ACM term premium

One download of `ACMTermPremium.xls` (sheet `ACM Daily`) holds the full daily history.
`ACMTP10` is split per series per year like §6.6; routine runs rewrite the current and previous
year, a backfill rewrites every year from `backfill_start` (2005).

### 6.8 `market_episodes` — episode cards

- Computed, no HTTP: one card per configured stress episode, built from the stored §6.6/§6.7
  rows, so it must run after them in the same tier.
- A card lists each series' level and its realized change over the **5 and 10 trading days**
  after the anchor close (the last close before the shock), with series ids and observation
  dates. An episode may set its own `horizons` (trading-day counts) to match a replay window;
  a 1-day line reads "1 trading day". Each card chunk carries an exact `horizon_days`,
  `doc_type: market_episode`, the last observation date over all the card's windows as
  `release_date`, and the `cross_asset_scenario` tag so the synthesizer's reserved
  scenario-study slots bring it in.
- Units follow `synthesis/vocab.py` (bp for rates and spreads, pts for VIX, pct for FX).
  Before the OAS window, cards use `BAA10Y` as a labelled IG stand-in; MOVE is not available
  and is left ungrounded.
- Episodes (16): Lehman, US downgrade, taper tantrum, China devaluation, Volmageddon, COVID,
  the 2021–22 AI peak-to-trough (also 221 days), Russia–Ukraine, 2022 CPI shock, the Aug–Oct
  2022 hawkish repricing (also 42 days), SVB, 2023 refunding sell-off, the July 2024 AI
  rotation, yen-carry unwind, DeepSeek (also 1 day), 2025 tariff shock.

---

## 7. Tier 4 — International official institutions

### 7.1 `imf_sdmx` — IMF SDMX 3.0

- `…/sdmx/3.0/data/dataflow/{agency}/{flow}/{version}/{key}`. Each configured flow/key is one
  fetch, flattened to numeric rows (no chunks). Configured keys are WEO real GDP growth, CPI
  inflation and general-government gross debt for the major economies.
- **Keys are positional** and their meaning differs per dataflow — they are curated in config in
  DSD order.
- **`~` (latest) is not reproducible**; the resolved dataflow version is pinned in the
  document fields.
- Keep `OBS_STATUS` (actual vs estimate vs forecast) and `UNIT_MULT` / `UNIT_MEASURE`; trust
  the DSD, not assumptions, for scale.

### 7.2 `imf_reports` — WEO and GFSR

- Each release is a multi-PDF bundle at the stable media path
  `…/publications/{weo|gfsr}/{year}/{month}/english/{component}.pdf` (foreword, execsum,
  ch1–ch3, statistical appendix). Discovery HEAD-probes a sentinel (ch1) per candidate issue,
  then enqueues every component that exists — components vary per release.
- WEO Chapter 1's Risk Assessment is the priority retrieval target. April/October main
  releases; January/July updates.

### 7.3 `bis_quarterly` — BIS Quarterly Review

- Full issue at `bis.org/publications/qr-{YYYYMM}.pdf` (bis.org moved from `r_qt{YYMM}.pdf` in
  2026; old URLs 301 to an HTML page). Article PDFs are slug-named, found by scraping the
  issue page and each article page.
- **Three issues a year — March, September, December**; discovery probes those months only.
- The markets section and the boxes (1–2 page deep dives, one chunk each) are the highest
  signal.

### 7.4 `bis_annual` — BIS Annual Economic Report

One ~150-page PDF a year (late June) at `bis.org/publications/aer-{YYYY}.pdf` (formerly
`publ/arpdf/ar{YYYY}e.pdf`), discovered by probing recent years newest first. The
financial-system chapter is the highest signal; the General Manager's introduction is a dense
summary chunk.

### 7.5 `boj_outlook` — BoJ Outlook Report

Quarterly (January/April/July/October outlook meetings) at `…/outlook/gor{YYMM}a.pdf`, English
edition under `/en/`. Projection tables go to the TS; the narrative is chunked on the stable
I–IV structure (activity, prices, risks, policy conduct). Drives the USD/JPY channel.

### 7.6 International hazards

1. SDMX positional keys and unpinned versions (§7.1).
2. Release identity is the issue, not any one PDF (IMF bundles).
3. BIS "quarterly" means three issues a year.
4. BoJ English editions are sufficient for retrieval; slugs are stable across languages.
5. Separate polite rate budgets per host (`api.imf.org`, `www.imf.org`, `www.bis.org`,
   `www.boj.or.jp`).

---

## 8. Tier 5 — House views (public flagships)

The dominant concern is **licensing**, not parsing, and each house has its own delivery
pattern. Content is read for internal analysis and cited by reference; answers paraphrase and
attribute rather than quote at length.

### 8.1 `jpm_eotm` — J.P. Morgan Eye on the Market (Cembalest)

The hub links stable `{slug}-amv.pdf` artifacts, run through the PDF pipeline. EOTM states it
"in no way constitutes J.P. Morgan Research", so it is ingested as
`public_with_attribution_required` with the author attribution preserved on every chunk. Each
PDF is multi-topic; token-budget sub-chunking approximates the per-topic split.

### 8.2 `blackrock` — BlackRock Investment Institute

The publications list is JS-rendered, but the individual landing pages (weekly commentary,
investment perspectives) are server-rendered with the latest full article, so discovery uses
configured direct URLs. Articles are tagged with the BII "mega force" they invoke (keyword
scan). Outlook PDFs go through the PDF pipeline.

### 8.3 `gs_tom` — Goldman Sachs Top of Mind

goldmansachs.com is a Next.js site that rejects bot agents and renders both the issue list and
article bodies client-side, so this handler uses the headless browser for both stages. One
issue is one deep dive (framing essay plus Q&A).

### 8.4 `ms_totm` — Morgan Stanley *Thoughts on the Market*

Every episode page carries a full text transcript (no audio processing). Speaker attribution is
kept in the chunk and fields so retrieval can weight by analyst specialty (credit vs equity
strategists).

### 8.5 `apollo` — Apollo Academy, *The Daily Spark*

WordPress: the `wp-json` posts API returns slug, date and rendered body in one call. Each post
(200–600 words) is one chunk.

### 8.6 House-view hazards

1. **Licence record** — every chunk carries its licence flag. The flag is provenance, not a
   control: no code path filters on it, so redistribution is the operator's responsibility.
2. **Slug URLs, no dates** — discovery and seen-set diffing on slugs; URLs cannot be
   constructed.
3. **Mirrored content** across regional sites and CDNs — canonicalize to one URL per house.
4. **Headless rendering only as fallback** (§2.8).
5. **Attribution at chunk level** (author/speaker/affiliation).
6. **Chart captions carry the claim** — keep them in the chunk text.
7. **Heavy disclaimers** — stripped by fixed-string matching.

---

## 9. Tier 6 — AI-specific sources

### 9A. Power and data-center electricity (`tier6a_power`)

#### 9A.1 / 9A.2 `eia_aeo`, `eia_steo` — EIA forecasts

One shared handler over EIA v2 REST (JSON → TS, no chunks). AEO has a `{year}` route,
HEAD-probed newest first; STEO is implicitly latest. The forecast vintage (`AEO2025`, `STEO`) is
recorded.

#### 9A.3 `iea` — IEA *Energy and AI*

Scrapes the report page for linked blob PDFs. IEA PDFs can be account-gated; if none are
reachable the handler is a no-op and EIA remains the power-forecast spine.

#### 9A.4 `pjm` — PJM Data Miner 2

Targets the DOM zone (Northern Virginia "Data Center Alley"). Needs a free-account
`Ocp-Apim-Subscription-Key`; without one (the default) the handler is a no-op with a clear note.

#### 9A.5 `ferc_714` — FERC Form 714

The annual demand **forecast** is the unique add over realized load data. Filings are CSV
through 2020 and XBRL from 2021; full parsing is left to PUDL (catalyst.coop). The handler
discovers the annual ZIP, persists it raw and emits an index document; there is no numeric ETL.

### 9B. Semiconductors, export controls, trade policy (`tier6b_chips`)

#### 9B.1 `federal_register` — BIS-Commerce rules and Section 232 proclamations

The highest-priority catalyst feed. The shared `common/federal_register.py` client runs two
configured queries:

- `conditions[agencies][]=industry-and-security-bureau` (BIS-Commerce rules);
- `conditions[presidential_document_type]=proclamation` + `conditions[term]=semiconductor`.

Stage B prefers the clean `raw_text_url` / rendered body over the PDF, and stamps covered
entities, technologies and countries on every chunk. Every Entity List change is also a Federal
Register rule, which gives §9B.2 redundancy.

#### 9B.2 `bis_entity_list` — Entity List

Cumulative state rather than an event stream: the landing page is ingested as a narrative
snapshot, and structured export links (CSV/XML) are surfaced for a downstream diff.

#### 9B.3 `sia` — SIA monthly sales

Scrapes the market-data archive for press releases; extracts total sales and YoY plus a short
narrative chunk. A blocked fetch is a no-op.

#### 9B.4 `tsmc` — TSMC monthly sales and capex

The highest-frequency foundry-demand proxy. Press releases are at
`pr.tsmc.com/english/latest-news`; tsmc.com rejects plain requests, so the handler uses the
HTTP-then-headless fallback and is a no-op if both are blocked.

### 9C. AI macro, adoption, geopolitics (`tier6c_aimacro`)

#### 9C.1 `census_btos` — Business Trends and Outlook Survey (AI supplement)

The only US-government high-frequency firm-level AI-adoption series. The Census EITS API returns
a header-row-plus-rows JSON array and is parameter-sensitive; a non-200 or empty response
degrades gracefully. Currently yields no documents.

#### 9C.2 `stanford_hai` — AI Index Report

~400-page annual PDF at `ai_index_report_{YYYY}.pdf`; discovery HEAD-probes recent years and
ingests the latest that exists.

#### 9C.3 / 9C.4 `gpr`, `epu` — Geopolitical Risk and Policy Uncertainty indices

Pure numeric spreadsheet downloads (GPR `.xls`, EPU `.xlsx`) through one shared
`index_download` handler, emitted as TS documents. The GPR-China and EPU China/Taiwan columns
are the quantitative inputs for Taiwan-tail weighting.

---

## 10. Tier 7 — Cross-asset stress and geopolitical scenarios

Tiers 1–6 give narrative, catalysts and series, but no dated source quantifies a **discrete
geopolitical event jointly across rates, credit, vol and FX**. Without one, a query such as
"Taiwan invasion tail, 6-month" retrieves only tangential evidence, the synthesizer correctly
refuses to invent magnitudes, and nothing passes the citation gate. Tier 7 adds authoritative,
dated, quantified shock studies the synthesizer can cite as analogs.

**Handler (`pinned_docs`).** These documents are not a feed: each lives at a stable, curated URL
with an authoritative release date known up front. Each `content.files[]` entry pins
`{url, publication, release_date, doc_type, is_pdf, section_headings?, scenario_family?,
priority?}` and runs through the shared PDF or HTML pipeline. The pinned `release_date` is what
lets the chunks pass the citation gate. A new vintage is a new file entry — no scraping logic.

| Endpoint | Document | Release date | Licence |
|---|---|---|---|
| `eba_esrb_stress` | EBA/ESRB 2025 EU-wide stress test — adverse scenario | 2025-01-14 | `public_research_free` |
| `imf_gfsr_geopolitical` | IMF GFSR Apr-2025 Ch.2 — geopolitical risks and asset prices | 2025-04-22 | `public_research_free` |
| `stlouisfed_taiwan` | St. Louis Fed Review — armed conflict over Taiwan | 2025-02-26 | `public_domain_us_gov` |
| `rhodium_taiwan_crisis` | Rhodium/Atlantic Council — sanctioning China in a Taiwan crisis | 2024-02-01 | `internal_analytical_use` |
| `hcss_taiwan_cost` | HCSS — Taiwan: the cost of conflict | 2024-03-01 | `internal_analytical_use` |
| `csis_taiwan_impact` | CSIS — Scared Strait | 2025-01-31 | `internal_analytical_use` |

Tier 7 is narrative only (no time-series emission). Realized-move evidence for analogs comes
from the market-episode cards (§6.8).
