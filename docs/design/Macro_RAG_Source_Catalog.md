# MacroRAG — Source Catalog

Which sources MacroRAG ingests, why, and how they cover the risk channels of faultline's
book. *How* each source is fetched and parsed is the
[Ingestion Design](Macro_RAG_Ingestion_Design.md). The authoritative endpoint list is
`configs/ingest/tier*.yaml`; if this page and the config disagree, the config wins.

---

## 1. Scope

MacroRAG supplies the **forward-looking, narrative and policy layer** of a stress scenario —
the direction and size of the next shock and why it would fire (FOMC guidance, Treasury
issuance, export-control catalysts, IMF/BIS and house-view risk framing, official stress
paths) — distilled into the citation-gated **shock vector** faultline consumes
([guide 05 §5](../guides/05-api-and-integration.md)).

Boundaries with the neighbouring systems:

- **faultline** owns live market data and pricing. MacroRAG ingests daily market series only
  to build dated **market-episode cards** (realized moves, usually over 5 and 10 days, around named stress
  events) and for time-series hydration — not as a market-data feed.
- **XbrlRag** owns company fundamentals (10-K/Q risk factors, capex guidance). MacroRAG
  supplies only the systematic spillover of a company shock, never company-specific legs.

All ingested sources are free: public-domain government output, free research, or free with
an account. Commercial research is used only where it is published openly, with its licence
flag on every chunk.

## 2. Risk channels

The book's P&L is dominated by a handful of macro channels; source weighting follows them.

| Channel | Shock fields | Main sources |
|---|---|---|
| Discount rate / term premium | `UST_2Y/5Y/10Y/30Y`, `UST`, `REAL_YIELD_10Y`, `TERM_PREMIUM_10Y` | FOMC/SEP/MPR, speeches, Treasury refunding/TBAC, CBO, DFAST paths, FRED/ACM daily |
| Rates volatility | `MOVE`, `MOVE_1Y/5Y/10Y/30Y` | FOMC guidance posture, refunding, DFAST |
| Credit spreads | `IG_OAS`, `HY_OAS` | Fed FSR, SLOOS, IMF GFSR, BIS QR, house views, FRED OAS / `BAA10Y` episodes |
| Equity level and vol | `EQUITY_US`, `VIX` | DFAST (equity path + history), GFSR, house views, FRED `VIXCLS` episodes |
| AI-capex cycle | — (narrative) | BlackRock, JPM EOTM, Apollo, GS, MS, BEA fixed investment, SIA/TSMC, Census BTOS |
| Power constraint | — (narrative) | EIA AEO/STEO, IEA, (FERC 714, PJM) |
| China/Taiwan and export controls | `USDTWD` (+ spillover to credit/vol) | Federal Register (BIS rules, Section 232), Entity List, OISP, Tier 7 Taiwan studies, GPR/EPU |
| FX and funding | `USDTWD`, `USDJPY`, `XCCY_BASIS` | BoJ Outlook, TIC, BIS QR/AER, IMF GFSR, FRED FX episodes |
| Growth and inflation | (context for all) | BEA, BLS, IMF WEO + SDMX, CBO |

The shock-field names, units and sign conventions are defined in `src/synthesis/vocab.py`
(19 fields).

## 3. Sources by tier

Bucket A = catalyst feed, B = periodic report (latest supersedes), C = rolling print or
commentary — see [Ingestion Design §3.2](Macro_RAG_Ingestion_Design.md#32-buckets).

### Tier 1 — Federal Reserve

| Endpoint | What | Bucket | Why |
|---|---|---|---|
| `press_monetary` | FOMC statements, minutes, SEP/dot plot, Monetary Policy Report | A | The single most important input for every rate-sensitive position |
| `speeches` | Chair and Governor speeches, testimony | A | Stance shifts between meetings |
| `financial_stability` | Financial Stability Report | B | Valuation and leverage narrative behind credit and tail scenarios |
| `dfast` | Supervisory stress-test scenarios + historic series | B | Official quantified paths; the historic series (1976-Q1 onward) backs the realized-episode backtest |
| `sloos` | Senior Loan Officer Survey | C | Credit-conditions confirmation |

### Tier 2 — U.S. Treasury

| Endpoint | What | Bucket | Why |
|---|---|---|---|
| `quarterly_refunding` | Financing estimates, policy statement, TBAC report/minutes, auction schedules | A | Long-end supply — the mechanism behind a term-premium shock |
| `oisp` | Outbound Investment Security Program rules and guidance | A | Taiwan-tail policy catalysts |
| `tic` | Treasury International Capital | C | Foreign demand for Treasuries; USD funding |

### Tier 3 — U.S. statistics and market series

| Endpoint | What | Bucket | Why |
|---|---|---|---|
| `bea` | NIPA: PCE prices, GDP, real PCE, private fixed investment, profits | C | Growth and inflation legs; AI-capex corroboration |
| `bls` | CPI, payrolls, earnings, unemployment, JOLTS, PPI | C | The inflation and labour prints that drive the rate path |
| `cbo` | Budget and Economic Outlook | B | Deficit trajectory and 10-year yield projection |
| `fred_daily` | VIX, IG/HY OAS, Baa–10Y, UST 2/5/10/30Y, 10Y TIPS, Kim–Wright term premium, SOFR, USD/TWD, USD/JPY (daily) | C | Raw material for the episode cards |
| `acm_daily` | NY Fed ACM 10Y term premium (daily) | C | `TERM_PREMIUM_10Y` measurement on episode cards |
| `market_episodes` | 16 computed episode cards | C | Evidence measured at the shock's own horizon (§4) |

### Tier 4 — International official institutions

| Endpoint | What | Bucket | Why |
|---|---|---|---|
| `imf_reports` | World Economic Outlook, Global Financial Stability Report | B | Global downside-risk framing; GFSR valuation and NBFI risk |
| `imf_sdmx` | WEO database (growth, inflation, debt) | C | Numeric backing for WEO narrative |
| `bis_quarterly` | BIS Quarterly Review | B | Markets view on spreads, term premia, funding |
| `bis_annual` | BIS Annual Economic Report | B | Cross-border USD liquidity, financial-system risk |
| `boj_outlook` | BoJ Outlook Report | B | USD/JPY channel |

### Tier 5 — House views (public flagships)

| Endpoint | What | Bucket | Why |
|---|---|---|---|
| `jpm_eotm` | J.P. Morgan *Eye on the Market* (Cembalest) | C | AI concentration, power and Taiwan risk framing |
| `blackrock` | BlackRock Investment Institute weekly + outlook | C | AI-capex financing and "mega forces" framing |
| `gs_tom` | Goldman Sachs *Top of Mind* | C | Agenda-setting deep dives |
| `ms_totm` | Morgan Stanley *Thoughts on the Market* | C | AI-infrastructure vs credit-issuance view |
| `apollo` | Apollo Academy *Daily Spark* | C | Highest-frequency AI-capex and credit commentary |

### Tier 6 — AI-specific

| Endpoint | What | Bucket | Why |
|---|---|---|---|
| `eia_aeo`, `eia_steo` | EIA long- and short-term outlooks | B | Data-center load trajectory |
| `iea` | IEA *Energy and AI* | B | Global data-center electricity outlook |
| `ferc_714` | FERC Form 714 demand forecasts | B | Forward demand (archive only — see §6) |
| `pjm` | PJM Data Miner 2, Dominion zone | C | Northern Virginia data-center load (needs a key — see §6) |
| `federal_register` | BIS-Commerce rules, Section 232 proclamations | A | The highest-priority catalyst feed for the Taiwan tail |
| `bis_entity_list` | Commerce Entity List | A | Named entities driving single-name impact |
| `sia` | SIA monthly global semiconductor sales | C | Directional demand confirmation |
| `tsmc` | TSMC monthly sales and capex | C | Highest-frequency foundry-demand proxy |
| `gpr`, `epu` | Geopolitical Risk and Economic Policy Uncertainty indices | C | Quantitative tail-weighting inputs |
| `census_btos` | Census BTOS AI-adoption supplement | C | Official high-frequency AI adoption |
| `stanford_hai` | Stanford HAI AI Index | B | Annual adoption/investment reference |

### Tier 7 — Cross-asset stress and geopolitical scenarios

Dated, quantified shock studies the synthesizer cites as analogs, so a geopolitical query can
ground a vector instead of returning nothing. Six pinned documents: the EBA/ESRB 2025 adverse
scenario, IMF GFSR April 2025 chapter 2 (geopolitical risk and asset prices), and Taiwan-conflict
studies from the St. Louis Fed, Rhodium/Atlantic Council, HCSS and CSIS. The think-tank items
carry `internal_analytical_use`. Details:
[Ingestion Design §10](Macro_RAG_Ingestion_Design.md#10-tier-7--cross-asset-stress-and-geopolitical-scenarios).

## 4. Market-episode library

Most of the corpus is research prose quoting quarterly or multi-quarter numbers. A 10-day shock
needs 10-day evidence, so MacroRAG builds one card per named stress window from the stored
daily series: each series' level and its realized move over the 5 and 10 trading days after
the anchor close. An episode can set its own horizons, so a card can match a replay window
faultline asks for: DeepSeek also has a 1-day horizon, the 2021–22 peak-to-trough a 221-day one
and the Aug–Oct 2022 repricing a 42-day one. All of a card's chunks are dated at its longest
window's end.

| Episode | Relevance |
|---|---|
| Lehman (2008), US downgrade (2011) | Credit tail; rates/vol divergence |
| 2021–22 AI and growth-equity peak-to-trough (221 days) | Multi-month AI-deflation / discount-rate repricing; no HY line, IG via `BAA10Y` |
| Aug–Oct 2022 hawkish real-rate repricing (42 days) | Inflation repricing of real yields; no HY line, IG via `BAA10Y` |
| July 2024 rotation out of AI leaders | Soft-CPI rotation: front-end rally, vol up, `USDJPY` down |
| Taper tantrum (2013), 2023 refunding sell-off, 2022 CPI shock | Rates sell-offs (most other cards are rallies) |
| China devaluation (2015), yen-carry unwind (2024) | Asia FX, `USDJPY`, vol spike |
| Volmageddon (2018) | Equity-vol shock with little credit move |
| COVID (2020), SVB (2023) | Credit/vol tail; rates-vol and funding stress |
| Russia–Ukraine (2022) | Geopolitical shock analog |
| DeepSeek (2025-01-27) | AI-specific single-name shock with measurable spillover — the analog for AI-bust and key-man scenarios |
| Tariff shock (2025-04) | Trade/export-control escalation |

Data limits that shape the cards:

- **ICE BofA OAS** is served by FRED only over a rolling ~3-year window (from 2023-10-16 when
  first ingested). Older years exist only as stored per-year documents; before the window,
  cards use `BAA10Y` as a labelled IG stand-in.
- **MOVE** is ICE-proprietary and not ingested; cards leave it ungrounded.
- Episode cards are old by nature. faultline applies a freshness gate to evidence, so cards
  only count there once faultline exempts `market_episode` evidence that passes the topical and
  horizon checks — a change handed to faultline, not yet merged on its side.

## 5. Coverage of faultline's book

faultline's core book is illustrative, not real positions. Its 38 USD trades span AI equities and power names, equity options,
hyperscaler IG bonds and CDS plus CDX IG, a UST/SOFR rates book (swap, swaptions, cap), and a
USD/TWD cross-currency swap plus a USD/JPY forward. Four of its eight stress scenarios request
MacroRAG legs (`taiwan_geopolitical`, `ai_bust`, `rate_capital`, `keyman_nvda`).

| Exposure | Fields | What MacroRAG holds | Remaining gap |
|---|---|---|---|
| AI single names | Spillover only (`VIX`, credit) | DeepSeek and tariff cards; house-view AI framing | Company legs are XbrlRag's |
| IG bonds, single-name CDS, CDX IG | `IG_OAS`, `HY_OAS` | FSR, BIS QR, house views, spread episodes | Little on the hyperscaler bond-issuance wave |
| Equity options | `VIX`, `EQUITY_US` | DFAST equity path, vol episodes, GFSR | — |
| UST, swap, swaptions, cap | `UST_*`, `MOVE*`, `REAL_YIELD_10Y`, `TERM_PREMIUM_10Y` | FOMC, speeches, refunding, DFAST, daily curve episodes | MOVE ungrounded; CBO empty |
| USD/TWD CCS, USD/JPY forward | `USDTWD`, `USDJPY`, `XCCY_BASIS` | Tier 7 Taiwan studies, BoJ, FX episodes | No realized basis series |
| AI power names | None in the vocabulary | EIA AEO/STEO, IEA | FERC 714 and PJM yield nothing |

`federal_register` is the largest narrative source by document count but about 12% of chunks.
A retrieval check found it crowds nothing out: it contributed no chunks to synthesis or general
macro questions and appeared only for an export-control question, where it belongs.

## 6. Known gaps and open items

- **Configured but empty:** `cbo`, `ferc_714`, `pjm` (no key provisioned), `census_btos`. Each
  needs its failure diagnosed before a re-run; filling `pjm` (capacity auctions bear on the AI
  power names) and the fuller FSR/GFSR history are the most useful.
- **Identified crawler:** every tier sends the MacroRAG User-Agent, so a site that refuses it is
  skipped rather than fetched as a browser. Expect Goldman Sachs Top of Mind among them.
- **Targeted prose:** AI-capex and hyperscaler bond-issuance commentary (Tier 5, BIS QR) — the
  credit story behind the hyperscaler bond and CDS legs.
- **Vocabulary:** two faultline scenarios (`real_rate_repricing`, `funding_basis_squeeze`) are
  driven partly by factors the vocabulary lacks — swap spread and CDS–bond basis. Adding them
  changes the faultline contract and needs a spec on both sides.
- **Not built by design:** page-image PDF extraction and power binding-constraint signals
  ([Production Hardening §C](Macro_RAG_Production_Hardening.md#c--ingestion-depth-not-built)).

## 7. Deliberately not ingested

- **Live market data and data faultline already pulls** — Treasury par curve, SOFR curve and
  swap nodes, DTCC/CFTC SDR, BIS `WS_*` bulk files, EIA hourly region data, Yahoo/CBOE quotes,
  EDGAR XBRL. (FRED daily series are ingested only for the episode cards.)
- **Paid or entitled research** — BloombergNEF, Gartner/IDC/SEMI databases, entitled sell-side
  desk research, Bridgewater. The free flagships cover the consensus range.
- **Lower-signal candidates** — Beige Book, FEDS papers, OFR, OMB/JCT, FDIC/OCC/FHFA, USTR
  (covered through the Federal Register), World Bank, OECD, ECB/BoE, PBoC, PIMCO/Vanguard,
  BofA FMS, ratings-agency detail, NBER, OECD.AI, Korea customs, ERCOT/MISO/CAISO, NERC, LBNL.
- **Taiwan customs export statistics** — a candidate if TSMC and SIA prove insufficient.
- **Conceptual and methodology material** ("how DFAST works") — not a retrieval concern.

## 8. Licensing and provenance

Every chunk carries publisher, publication, `release_date`, URL, retrieval date, content hash
and a licence flag. Government output is public domain. House views are used for internal
analysis, cited by reference and paraphrased rather than quoted at length; J.P. Morgan EOTM
keeps its author attribution on every chunk; Tier 7 think-tank studies are internal-use only.
