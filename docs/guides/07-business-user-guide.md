# 07 — Business User Guide

A non-technical guide for analysts and risk users. It explains **what MacroRAG does**,
**how to use it through the web UI**, how to **trust the answers**, and how to turn a
scenario into the **shock vectors** the stress engine consumes. No coding required.

> If you need to install or run the system, hand this to your operator and point them
> at [01 – Setup](01-setup-and-prerequisites.md) and [06 – Web UI](06-ui.md).

---

## 1. What is MacroRAG?

MacroRAG reads the world's macro research — central banks (the Fed, BoJ),
statistical agencies (BLS, BEA, Census), international bodies (IMF, BIS), sell-side
house views, and energy/semiconductor supply-chain sources — and lets you **ask
questions in plain English** and get **answers with citations**.

It does two distinct jobs:

1. **Cited Q&A (for humans).** Ask a macro question; get a concise answer where every
   claim is traceable to a dated source document.
2. **Shock vectors (for the stress engine).** Describe a scenario (e.g. "a Taiwan
   escalation"); get a structured, citation-backed set of market moves (rate, spread,
   FX, volatility shocks) that the faultline stress-testing engine can run.

Two things make it different from a generic chatbot:

- **Provenance** — answers cite specific, dated documents. If it can't support a
  claim, it drops it rather than guessing.
- **Freshness & exact numbers** — it keeps the precise figures (e.g. a DFAST
  unemployment path) and prefers the most recent releases.

---

## 2. Getting in

Open the web UI your team hosts (in local dev this is **http://localhost:5173**). If
prompted for an **API key**, paste the one your operator gave you — this identifies
you and unlocks the screens you're allowed to use.

The app has three areas in the side navigation:

- **Business** — for everyday analyst work (Ask, Factors, Scenario Studio, Corpus).
- **Maintenance** and **System** — operator/technical screens; you can ignore these
  unless you manage the corpus.

---

## 3. Asking a question (the **Ask** screen)

1. Type a question, e.g. *"What did the Fed's latest stress test assume for the
   unemployment rate in the severely adverse scenario?"*
2. (Optional) Narrow the search with **filters** — by source type, theme (e.g. AI,
   China, semiconductors), risk factor (e.g. credit spreads, inflation), publisher, or
   date range. Filters make answers more focused; leave them empty to search broadly.
3. The answer **streams in** with an **evidence rail** beside it.
4. Click any **citation** to open the source chunk — its text, publisher, publication
   and **release date** — so you can verify the claim yourself.
5. If numbers are involved, the answer hydrates the **exact time-series** (e.g. the
   full unemployment path) rather than paraphrasing them.
6. Use **thumbs up / down** to give feedback. This is not cosmetic — it feeds the
   team's quality tracking and helps improve retrieval over time.

**Reading the answer well:**
- Treat the **citations as the product.** A claim without a citation should be
  treated with caution.
- Check the **release date** on cited sources — for fast-moving topics, prefer recent
  ones.
- If the system says it doesn't have enough evidence, that's a feature: it would
  rather decline than invent. Try rephrasing, widening filters, or asking your
  operator whether the relevant source has been ingested.

---

## 4. Building a scenario (the **Scenario Studio** screen)

This is where a narrative scenario becomes **machine-readable market shocks**.

1. Describe the scenario in plain English, e.g. *"A cross-strait escalation disrupts
   Taiwan chip exports over a 10-day horizon."*
2. The studio produces a **shock vector** — a small table of market moves such as:
   - government bond yields (e.g. `UST_10Y`, in basis points),
   - credit spreads (`IG_OAS`, `HY_OAS`, in basis points),
   - volatility (`VIX` in points, `MOVE` for rates vol),
   - the US equity market (`EQUITY_US`, in percent),
   - the real-yield and term-premium parts of a rates move (`REAL_YIELD_10Y`,
     `TERM_PREMIUM_10Y`, in basis points),
   - currency moves (`USDTWD`, `USDJPY`, in percent).
   You can pin the **horizon** (e.g. 10 days) so the moves match how long the shock lasts.
   Under **Options** you can also say which way you expect each market to move (↑ or ↓):
   a field that comes back moving the other way is dropped rather than shown with the
   wrong sign.
3. Every number is **citation-gated**: if the system can't back a figure with a dated
   source, it **drops that field** instead of fabricating it. So the vector you see is
   the part it can defend.
4. Each scenario is stamped with a **corpus snapshot id** (`world_view_id`) — a label
   for exactly what the system "knew" when it produced the vector. It's a provenance /
   freshness stamp for audit (and to tell whether two vectors used the same corpus), not
   a time machine: once the corpus is refreshed, the old state isn't kept, so you can't
   re-run against a superseded snapshot.
5. **Compare** scenarios side by side and **export** the vector for the stress engine.

Think of the shock vector as a **cited bridge** from "here's a story about the world"
to "here's what to shock in the risk model, and here's the evidence for each move."

---

## 5. Finding the risk factors that matter (the **Factors** screen)

Describe a portfolio or theme (e.g. *"AI hyperscalers and the chip supply chain"*) and
Factors returns the macro **risk factors** worth stress-testing — ranked, each with
citations. It is a good first step before Scenario Studio: find the drivers, then
quantify them.

---

## 6. Browsing what's in the system (the **Corpus** screen)

Use Corpus to see **what the system has read**:

- Browse documents by **facets** — source tier, theme, risk factor, publisher, date.
- Open a document to see its sections and any numeric series it carries.
- Browse the **time-series catalog** to see which exact data series are available.

This is the quickest way to sanity-check coverage before relying on an answer ("do we
even have the latest IMF outlook ingested?").

---

## 7. Glossary

| Term | Plain meaning |
|---|---|
| **Citation / chunk** | A specific passage of a source document an answer is built from |
| **Provenance** | The trail back to the original dated source |
| **Shock vector** | A structured set of market moves (rates, spreads, FX, vol) for stress testing |
| **Citation-gating** | Dropping any shock figure that can't be backed by a dated source |
| **`world_view_id`** | A provenance/freshness label for what the corpus contained when a vector was made (a stamp for audit, not a corpus you can query later) |
| **Tier** | A family of sources (Tier 1 = Fed, Tier 4 = international bodies, etc.) |
| **Factor tag** | A risk channel a document touches (e.g. credit spreads, inflation, AI capex) |
| **Entity tag** | A theme/actor (e.g. AI, China, semiconductors) |
| **DFAST / SEP** | Fed stress-test scenarios / the Fed's Summary of Economic Projections |
| **Retrieval / rerank** | How the system finds and orders the most relevant passages |

---

## 8. Troubleshooting (for business users)

| What you see | What it means | What to do |
|---|---|---|
| "Not enough evidence to answer" | The corpus doesn't strongly support a grounded answer | Rephrase; widen/clear filters; ask your operator if the source is ingested |
| Answer cites an old document | A newer source may not be ingested yet | Check the **Corpus** screen; ask your operator to run an incremental update |
| A shock-vector field you expected is missing | It was dropped (no dated source backed it, or it failed a check such as the wrong direction), or the evidence never covered it — the field's row says which | Add detail to the scenario; confirm the supporting source exists in Corpus |
| A field carries an orange or red tag | Orange tags (e.g. `analog`, `horizon_unknown`) are cautions; a red tag is a hard failure, normally dropped | Down-weight flagged fields; ask your operator about red tags |
| The page asks for an API key | Access control is on | Get a key from your operator; admin screens need an admin key |
| Answer seems wrong | Possible retrieval miss | Use thumbs-down with a note; open the citations to see what it used; tell your operator |
| Page is blank / nothing loads | The backend service may be down | Tell your operator (they restart the API; see [06](06-ui.md)) |

For the technical view of any of this, see the [Guides index](README.md).
