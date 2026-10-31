# MacroRAG — Positioning & Capability Brief

**Canonical home for the external-facing case.** Three parts, as briefed:

1. **Functional & business advantages** — grounded shock vectors, the controls that make
   them traceable, and the fit with stress-engine scenario generation and valuation.
2. **AI harness & AI-native SDLC practices** — what this repo demonstrates about building
   an LLM product properly.
3. **Unique selling points** — the short list.

Every claim below is traceable to code in this repo; file references are given so the
brief can be fact-checked rather than trusted. A closing section states plainly what the
system does **not** claim.

> **On overlap with the rest of the docs.** This brief is written to be read standalone by
> someone outside the project, so it deliberately restates things the technical docs own —
> that repetition is the point of a brief, not an oversight. To keep it from drifting, it
> **cites rather than re-specifies**: no endpoint list, no config table, no flag reference,
> and a fact-check appendix pointing at the code behind each claim. If you need the details,
> the owning docs are the operator guides
> ([index](guides/README.md), [ownership map](../CLAUDE.md#documentation-map--one-owner-per-topic)).
> When a number here goes stale, fix it here — do not add a second copy elsewhere.
>
> Figures quoted below (test count, corpus spans) were checked against the repo on
> **2026-10-17**. Re-verify with `pytest test/ --collect-only` before reusing them.

---

# Part 1 — The business case: shock vectors you can defend

## 1.1 The problem it removes

A stress-testing programme has a hand-off that nobody owns. The Fed publishes a DFAST
severely-adverse path as a PDF and a spreadsheet. The IMF, the BIS, the EIA and a dozen
sell-side desks publish narrative views. Somewhere downstream, an analyst reads all of it
and types a set of factor moves into a scenario file: 10Y up 120bp, HY OAS wider 450bp,
VIX to 45.

That typing step is the weakest link in the chain. It is slow, it is one person's
judgement, it leaves no record of *why* each number was chosen, and it cannot be
re-run when a new Fed release lands. When the model-risk function asks "where did the
450 come from?", the honest answer is usually "from the analyst".

MacroRAG replaces that step with a **machine-readable, citation-gated contract**.

## 1.2 What comes out

A shock vector: a set of named factor moves over a stated horizon, where each field is

| Property | Guarantee |
|---|---|
| **Named** | drawn from a fixed 19-field vocabulary, not free text ([`vocab.py`](../src/synthesis/vocab.py)) |
| **Signed** | with a documented sign convention per field ("positive means yields rise") |
| **Unit-checked** | `bp` / `pct` / `pts` / `rel` fixed per field; a wrong unit is rejected, never coerced |
| **Cited** | `chunk_ids` pointing at dated source passages that are returned in the same response |
| **Labelled** | `grounding_mode: direct \| analog` — is this the scenario's own number, or a proxy? |
| **Flagged** | `validation[]` codes when the number is implausible or fights the rest of the vector |

The 19 fields cover the macro envelope a rates/credit/vol book actually needs: the UST
curve (2Y/5Y/10Y/30Y plus a parallel shift), the 10Y real yield and term premium (so a
rates move splits into its parts), IG and HY OAS, the rates-vol **term structure**
(aggregate MOVE plus 1Y/5Y/10Y/30Y expiry nodes, so a swaption or cap book sees a vol curve
rather than one scalar), VIX, a broad US equity **level**, USDTWD, USDJPY and
cross-currency basis.

Short-horizon shocks need short-horizon evidence, so the corpus also carries
**market-episode cards**: the realized 5- and 10-trading-day moves of the same series around
named stress events (Lehman, COVID, SVB, the 2024 yen-carry unwind, DeepSeek, the 2025
tariff shock and others), computed from stored daily data and citable like any document.

The same call also returns the ordinary cited prose answer. Humans and the stress engine
read the **same evidence set from the same pipeline** — there is no separate "narrative
version" that can drift from the numbers.

## 1.3 Grounding is enforced by code, not requested in a prompt

This is the central design decision. Asking a model to "only cite real sources" is not a
control. MacroRAG runs the model's output through a deterministic ladder, and each rung
is ordinary Python that a reviewer can read:

**1. Vocabulary validation** ([`synthesizer.py`](../src/synthesis/synthesizer.py)) — an
unknown field name, an invalid unit for that field, or a non-numeric magnitude is dropped.
A field emitted as exactly `0` is also dropped: a zero is a positive claim of "no move",
which is almost always the model padding a factor it could not anchor. An absent field
already means "no shock applied", so omitting it is both truthful and operationally
identical.

**2. The citation gate** ([`gate.py`](../src/synthesis/gate.py)) — every field must cite at
least `min_citation_chunks` chunks that are (a) present in the response's `chunks[]` and
(b) carry a populated `release_date`. Fields that fail are **dropped, not flagged**. This
is the hard bound on machine-consumed output: nothing reaches the stress engine that
cannot be walked back to a dated passage.

**3. Numeric validation & coherence** ([`validate.py`](../src/synthesis/validate.py)) —
three cheap, dependency-free checks on what survived:

- **Plausibility band.** Each magnitude is checked against a per-field ceiling, scaled with
  the scenario horizon (vol grows roughly with √time, capped at 3×). These are generous by
  design — they catch sign-flipped typos, unit confusion and fat-fingered zeros, not
  calibration error.
- **Series anchor.** When the answer hydrated a stored numeric series that maps to the
  field (`treasury_10y` → `UST_10Y`, `vix` → `VIX`), the emitted move is compared against
  that series' observed span. A move that dwarfs history is flagged.
- **Cross-field coherence.** A small, defensible rule set: IG and HY OAS should move the
  same way; equity vol and HY credit share direction; the equity **level** moves opposite
  to VIX in a risk event; rates-vol nodes should not fight their own term structure. A
  violation flags both participating fields.
- **Evidence horizon.** A field whose evidence covers a very different horizon from the one
  requested — a multi-quarter stress-test peak offered as a 10-day shock — is caught.
- **Direction.** When the caller states the expected sign of each field, a field that moves
  the other way is caught.

Conflicts between fields are **annotated, not resolved** — the consumer sees both sides
rather than a silently picked winner. Unambiguous failures (implausible magnitude, unit-scale
confusion, horizon or direction mismatch) are **dropped** in the shipped configuration, so a
consumer that never reads validation codes still never prices them.

**4. Grounding reconciliation** — a deterministic backstop that upgrades a field from
`analog` to `direct` only when the scenario's own numeric path was in the hydrated data
*and* the emitted shock agrees with that path's stress direction. It only ever upgrades, so
it cannot over-claim.

**5. Refusal floor** — upstream of all of this, a relevance floor (`min_rerank_score`) means
that when nothing sufficiently relevant is retrieved, the pipeline refuses instead of
generating. An honest empty answer beats a confident wrong one.

## 1.4 An empty vector is explained, not mysterious

Every synthesis call returns `synthesis_diagnostics`
([`diagnostics.py`](../src/synthesis/diagnostics.py)): a one-word `outcome`
(`grounded` / `partial` / `all_dropped` / `llm_empty` / `no_chunks` / `upstream_refused`),
a `coverage_gap` boolean, the fields the model emitted before validation, the fields that
survived, which survived only via analog grounding, and a per-field record of **which stage
dropped it and why**.

That turns "the vector came back empty" from a support ticket into a branch the consumer
can code against — fall back to prose and the nearest evidence, and know whether the cause
was a thin corpus, an ungrounded model, or an upstream refusal.

## 1.5 Traceability: the controls that make an answer defensible six months later

Traceability here is not a slogan; it is a set of stamps and logs.

**A content-addressed corpus identity.** Every index build computes a `world_view_id` of
the form `macro-<YYYY-MM-DD>-<12-hex>`, where the hex is a digest over the sorted
`(chunk_id, content_hash)` pairs of the whole corpus
([`manifest.py`](../src/utils/manifest.py)). Consequences that matter: an unchanged corpus
re-indexed keeps the **same** id, and two different corpora built on the same day cannot
collide. Every shock vector and factor response carries the id it was synthesized under.

**A build manifest.** Each build records pipeline version, embedding model / dimension /
grounding-prefix setting, reranker model, the LLM tier assignment, a hash of the **entire
prompt registry**, the collection name, chunk and observation counts, and the corpus
digest. `is_stale` compares the embedding-relevant signature and tells you when the vector
index must be rebuilt rather than letting a mismatched index serve quietly.

**An append-only build history.** `GET /admin/manifest/history` lists builds with a
field-level diff against the previous one; `GET /admin/manifest/verify?world_view=…` answers
the question an auditor actually asks about an old result — *was this ever our corpus, and
is it still the live one?*

**Prompt provenance on every call.** Prompts are versioned files under `prompts/`, not
string literals. Each resolved prompt carries a `variant_id` and a `content_hash`, and both
are stamped into the per-call record, so a given number is attributable to an exact prompt
version ([`registry.py`](../src/llm/registry.py)).

**A per-query trace.** One JSONL line per query ([`trace.py`](../src/utils/trace.py)): route
taken, filters extracted, every retrieved chunk with its raw and rerank scores, hydrated
series, refusal/verification outcome, stage timings, token and cost accounting per call
(with model, provider, purpose, prompt variant and hash), any model escalations, and any
suspected prompt-injection markers. The synthesis trace adds the full grounding diagnostics.

**Resolvable citations.** `GET /citations/{chunk_id}` and the batch `POST /citations` return
the chunk text, publisher, publication, section, URL and release date. The walk from a
number in the stress engine back to the paragraph in a Fed release is a single API call.

**Low-variance by configuration.** Sampling temperature defaults to `0.1`; runtime config is
committed, typed and validated (a mistyped key raises rather than silently defaulting); and
the gate, the numeric validation and the coherence rules are pure deterministic code — the
parts that decide what ships are not sampled at all.

## 1.6 Fit with stress-engine scenario generation and valuation

The system was designed against a downstream consumer, and it shows in the surface area.

**Contract-first, published, machine-readable.** `GET /synthesis/schema` returns every valid
field with its allowed units, the preferred emitted unit, and the documented positive-sign
meaning — plus the definitions of `direct` and `analog`. A scenario-generation agent can
construct and interpret shock vectors without scraping a prompt or guessing a field name.

**Structured controls, not prose steering.** The synthesis call accepts:

- `fields` — restrict the vector to the factors this book actually carries;
- `horizon_days` — pin the horizon structurally, with every move defined as the cumulative
  change over it (so "10-day shock" is a parameter, not an adjective in a sentence);
- `scenario` — extra narrative folded into **both** retrieval and synthesis;
- `directions` — the expected sign per field, used both to steer evidence selection and to
  reject a field that moves the wrong way.

**Three call paths, one behaviour.** REST, an MCP server (`macro_synthesize_shock_vector`,
`macro_discover_factors`, `macro_get_citations`, `macro_vocabulary`, `macro_shock_schema`),
and a CLI. The MCP surface matters for the intended use: an LLM-driven scenario generator
calls MacroRAG as a **tool**, gets a typed vector back, and hands it to the valuation
engine.

**Weighting instead of blind trust.** `grounding_mode` and `validation[]` give the stress
engine a principled way to down-weight a proxy-grounded or flagged field rather than making
a binary keep/discard call on the whole vector.

**Clean division of responsibility on freshness.** MacroRAG guarantees every cited chunk
*has* a release date. Whether that date is fresh enough relative to a given valuation date
is the consumer's policy call, and is deliberately left there.

**Factor discovery as the front door.** `POST /factors` takes a free-text portfolio or theme
and returns the ranked macro risk factors relevant to it, cited and gated on the same
machinery — so scenario design can start from "what should I even be shocking for this
book?" before it gets to magnitudes.

## 1.7 The evidence that it works — and the discipline of keeping two kinds apart

Most RAG products present one accuracy number. MacroRAG maintains three independent
families of evidence and refuses to average the incompatible ones.

**Transcription fidelity** ([`shock_eval.py`](../src/eval/shock_eval.py)) — scores an emitted
vector against *projected* official paths, the DFAST/EBA baseline→adverse delta. This
measures whether the Fed's published scenario was read and transcribed correctly. It is a
legitimate and important measure, and it is **not** forecast skill.

**Realized-episode backtest** ([`episodes.py`](../src/eval/episodes.py)) — scores against what
markets *actually did*, using 50 years of realized quarterly history already in the store
(1976-Q1 → 2025-Q4, 28 variables), reduced to peak deviation from a pre-crisis reference
level. It runs in two separately-named modes whose metrics are structurally prevented from
merging:

- `grounding_fidelity` — evidence visible. *Can the system turn a described scenario into
  the right numbers when the relevant research is retrievable?*
- `ex_ante_skill` — retrieval hard-filtered to `release_date` strictly before the episode
  opens, so only documents that existed at the time are visible. This is genuine forecast
  skill, and it is **bounded by corpus depth, not model quality**: the corpus is mostly
  recent, so for older episodes there is little to retrieve and the system correctly
  abstains. A high `ex_ante_skill_abstention_rate` is an honest statement about the corpus,
  reported alongside accuracy and never folded into it.

**Self-validation with no human labelling**
([`selfcheck.py`](../src/eval/selfcheck.py)) — the time-series store is populated by
deterministic parsers at ingest; the answer path runs embed → retrieve → rerank → generate.
Those are **independent code paths over the same source documents**, so the store is a
machine-checkable answer key. Every stored `(variable, scenario, period, value)` row
templates a question whose answer is already known, and each case is scored on two
independent signals — did hydration surface the right row, and does the prose state the
right number — which localises a failure to retrieval, generation, or the store. No judge
model, no labelling cost, fully deterministic.

Alongside it, **negative controls**: probes for variables and scenarios that do not exist,
and false-premise questions. Outcomes are classified four ways rather than pass/fail,
because correcting the premise while naming a real number is the *best* behaviour and
collapsing it into a failure would punish it. Only `fabricated` counts against the system.

---

# Part 2 — How it is built: AI harness and AI-native SDLC practice

This section is the engineering case. It is organised by practice, and each one is
observable in the repository.

## 2.1 Every external dependency sits behind an interface — and has a fake

Embedder, vector store, reranker, LLM, object storage and the HTTP layer each have a
`base.py` contract and a test double: a hashing embedder, an in-memory vector store, an
identity reranker, a scripted `FakeLLM`, an in-memory object store, a fake Redis.

The result is the property that makes everything else possible: **803 tests that use no
torch, no GPU, no model download and no network**, running on a plain CI runner in seconds.
The house rule is explicit — adding an external dependency means defining or extending the
interface and providing a fake.

## 2.2 Prompts are versioned artifacts, not string literals

Prompts live in a registry at the repository root — `prompts/system/<role>.md` and
`prompts/tasks/<task>/{base.md, base.meta.yaml, schema.json}` — covering the eight LLM tasks
under management (answer, verify, plan, plan_execute, next_hop, synthesize,
discover_factors, judge).

- `resolve(task, provider, model, pin)` follows a fallback chain — explicit pin, then
  provider + model-series at the highest active version, then base — so N providers × M
  models never requires N×M authored prompts.
- Every resolved prompt carries a `variant_id` and a `content_hash`, stamped into the call
  log and trace.
- Adding a model-series variant is **two files and no code change**.
- A hash over the entire registry is folded into the index manifest, so prompt drift is
  detectable at the corpus level, not just per call.

## 2.3 Structured output with a validate → repair → fail-soft ladder

`complete_json` parses tolerantly, validates against the task's JSON Schema, and on failure
issues exactly **one repair retry** (tagged `:repair` in the usage log) before falling back
to a best-effort parse. Fail-soft behaviour is preserved, but a silent `{}` becomes an
observable, retried event.

`schema_health.py` records the outcome per task — `ok` / `repaired` / `failed` — feeding
`GET /admin/schema-health` and a `repair_rate` metric in the regression matrix. Schemas are
deliberately permissive: they catch gross drift without tripping the fail-soft paths.

## 2.4 Doer ≠ judge, and deterministic gates outrank model judgement

The separation is structural:

- Generation and **faithfulness verification** are different tasks with different prompts.
- The **citation gate** and the **numeric/coherence validation** are pure code, and they are
  what actually bounds machine-consumed output.
- The relevance floor produces refusals without consulting a model.
- The LLM judge is used for **aggregate quality tracking**, never as the shipping gate.

Stated plainly: no model, including the one that wrote the number, gets to decide whether
that number ships.

## 2.5 Evaluation is a subsystem, not a script

- **Retrieval metrics** — hit@k, recall@k, MRR against two golden sets (user-facing prose
  and stress-backend shock vectors).
- **LLM-judged answer quality** — aggregate faithfulness and relevancy, opt-in via `--judge`.
- **Prompt × model regression matrix** — the Cartesian product of prompt variants and
  models, each cell scored over the golden set and **diffed against a saved baseline**, with
  correct handling of metrics where lower is better. `--save-baseline` / `--fail-on-regression`.
- **Enforced drift gate** — `eval --fail-on-regression` exits non-zero; the admin eval job
  fires an alert and fails the job.
- **Shock backtests and self-validation** as described in §1.7.
- **Feedback loop with a human in it** — positive, chunk-citing user feedback is reconciled
  into golden *candidates* and is **never auto-promoted** into the golden set.

The honest gap is documented rather than hidden: a *serving* gate that blocks a
freshly-built but regressed index from being served needs an index staging/promotion
workflow, which is documented as not built, with the reason.

## 2.6 Provider portability by contract

One narrow abstract method (`complete`); everything else is concrete shared behaviour.
`build_llm` selects the client purely from `settings.provider` with deferred imports.
Provider idioms stay **inside** the client — `max_completion_tokens` versus `max_tokens`,
an ephemeral cache breakpoint on the system prefix, omitting `temperature` for models that
reject it — and never leak into prompts or call sites.

There are **no hardcoded model ids**. Tiers (`plan`, `answer`, `complex`, optional
`escalation`) resolve from a per-provider profile block, so switching provider remaps every
tier at once. Pricing is table-driven by longest-prefix match, and a new escalation target
needs a pricing row to keep cost accounting complete.

## 2.7 Tiered routing and quality-signal escalation

Model spend is matched to task difficulty: a capable tier for planning, routing and
verification (these drive retrieval quality); a cheap tier for simple generation; a frontier
tier for complex routes, synthesis and factor discovery.

Escalation is **quality-signal driven**: when a cheap pass shows a deterioration signal
(too many claims dropped by the verifier, too many shock fields gated away, or none
surviving), the pass is re-run on a higher tier
and the result is kept **only if it is better**. Every upgrade is recorded in the trace's
`escalations[]` for longitudinal analysis. It is off by default, and the policy checks both
a per-request cap and the live budget before escalating.

## 2.8 Cost is a control, not just telemetry

- **Per-call priced accounting** — model, provider, purpose, tokens, latency, cost, prompt
  variant and hash; cache reads and writes priced at their own rates. Settings refuse to load
  while any configured model lacks a rate-card row, and a call that still reaches an unpriced
  model marks the answer's cost incomplete rather than free.
- **Hard daily ceilings**, per-principal and global, enforced as **admission control**: a
  request over its ceiling returns **HTTP 402** before any spend occurs, and its measured
  cost is recorded on completion.
- **A per-request soft cap** that raises an alert rather than blocking, for the unusually
  expensive one-off.
- Counters are date-keyed (self-expiring at midnight) and either in-process or Redis-shared
  across replicas.
- Prometheus counters for calls, tokens and USD.

## 2.9 Security taken seriously in an ingest-from-the-internet system

- **Prompt-injection defence** ([`sanitize.py`](../src/llm/sanitize.py)) — retrieved chunk
  text is wrapped in an explicit untrusted-data fence with an isolation directive, and any
  attempt to forge or close that fence from inside is neutralised. Detection patterns are
  tuned to require an instruction-ish object, so ordinary macro prose ("markets disregarded
  the data") does not trip them. Hits are logged, counted in Prometheus and stamped into the
  query trace. The module documents itself as defence-in-depth, with the deterministic
  citation gate named as the actual hard bound.
- **Secrets** — gitignored `.env`, plus a `<NAME>_FILE` convention for Docker/Kubernetes
  secret mounts, with the file taking precedence, so keys need not sit in the process
  environment.
- **Bounded inputs** — every caller-supplied string and list has a cap; a breach returns
  **422** rather than consuming memory or LLM budget.
- **Admission control** — a bounded semaphore in front of the CPU-bound rerank stage returns
  **503** on saturation instead of queueing without limit.
- **Job locking** — a conflicting store-mutating job returns **409**.
- **Configurable CORS**, defaulting to same-origin only; a strict CSP on the UI image.
- **Dependency CVE scanning** in CI (advisory, so a new feed entry does not block a merge).

## 2.10 Resilience and horizontal-scale state

Provider calls are wrapped by a shared retry-with-backoff and circuit breaker, with the
policy centralised in one module rather than duplicated per client — a tripped breaker fails
fast instead of hammering a provider that is down.

Shared state (job store and locks, rate limiting, cost counters, semantic cache) is
independently switchable per feature between in-process and Redis, so a single-node
deployment carries no Redis dependency and a multi-replica one shares state correctly.

## 2.11 Observability

Prometheus `/metrics` (request counts and a duration histogram, LLM calls, tokens and USD,
schema outcomes, suspected injections), structured JSON logging, the per-query JSONL trace,
and an OpenTelemetry configuration in `infra/`. The UI ships a **Trace Explorer** with an
end-to-end waterfall, score evolution across retrieval stages, and evidence drill-down — the
same trace data, made operable.

## 2.12 The development-time harness — AI-assisted SDLC, governed

This is the part most projects skip. The repository governs how AI agents work *on* it:

- **Layered context.** A root `CLAUDE.md` acts as a whole-repo map; module-level `CLAUDE.md`
  files (`src/ingest`, `src/llm`, `ui`) load automatically when an agent works in them. Lean, scoped, and
  maintained as conventions change — context engineering rather than one giant prompt file.
- **Deterministic hooks** (`.claude/hooks/`) — a **PreToolUse secrets guard** that vetoes
  reading or editing the dotenv file; a **PostToolUse lint/auto-fix** on every edited `.py`
  so the CI lint gate stays green; and a **Stop hook** that runs the coding-standards check
  and blocks ending a session on a red test subset. Guardrails the agent cannot talk its way past, because they are the
  harness, not the model.
- **Test-first changes with locked specs.** Every change starts as a written spec
  (`docs/specs/<id>.md`) and spec tests that are proven red, then hash-locked and merged
  before implementation begins. The implementing agent cannot edit a locked test; if a test
  looks wrong it files a dispute. CI checks the locks and classifies each PR by the files it
  touches, so a spec change and its implementation cannot ride in together.
- **An external rubric with a re-audit trigger.** The harness is scored against a reusable
  9-dimension agent-engineering standard that is re-applied after each major model release,
  with the instruction to **prune scaffolding that is no longer load-bearing**, not merely to
  add more.
- **Design docs state limits, not just features.** Each design document describes the
  current system together with what is deliberately not built and why, and the decisions
  that still constrain the code (such as the live-only corpus stamp).

## 2.13 CI/CD and packaging

GitHub Actions runs on every push and pull request: **lint** (a blocking, critical-only
ruff subset — real bugs, not style — beside an advisory coding-standards job whose
burn-down list of existing findings may only shrink),
**static type checking** (`mypy` held at zero errors, with suppressions scoped per module
*and* per error code so a non-zero exit always means something real — a blanket ignore is
what teaches a team to stop reading the output), **tests**, **UI type-check and build**,
**both container images built**, and an advisory dependency audit.

Two images ship. The backend is **deliberately lightweight — no torch** — because the heavy
embedder and reranker are factored out into a shared inference service that one machine can
run once and several projects can share. The UI is an nginx SPA that reverse-proxies `/api`,
with the backend URL supplied as a single runtime variable so nothing is baked into the
bundle.

Deployment is covered by Docker Compose and Helm, a build → gate → push script for ECR
(including arm64/Graviton cross-building), a backup/restore script for the durable local
state, and a `deploy_mode: local | aws` switch that moves corpus storage between the local
filesystem and S3 **behind a single seam**, byte-identical in local mode.

## 2.14 Reproducibility discipline in the build itself

A `PIPELINE_VERSION` that is bumped whenever a change requires a full re-embed, with
staleness detection that compares the embedding signature. Content-hash-based **incremental
indexing** that embeds only new and changed chunks and deletes departed ones, with a
`--dry-run` that previews the plan. Runtime configuration committed with its tuned overrides
and inline rationale comments, loaded through typed models so a mistyped key raises.

---

# Part 3 — Unique selling points

**1. The output is a contract, not a chat answer.** Nineteen named fields, fixed units,
documented sign conventions, published as a machine-readable schema endpoint. A downstream
engine integrates against a type, not against prose.

**2. Citations are enforced by code, not requested in a prompt.** A field that cannot cite a
dated, returned source chunk is dropped before it leaves the process — and the response tells
you which fields were dropped and at which stage.

**3. The numbers are sanity-checked, not just sourced.** Horizon-scaled plausibility bands,
anchoring against 50 years of realized history held in the same system, and cross-field
coherence rules, so the vector has to hang together as a single scenario rather than as
nineteen independent guesses.

**4. Uncertainty is expressed, not hidden.** `direct` versus `analog` grounding and per-field
validation codes travel with the data, so a consumer can down-weight rather than face a
binary trust decision.

**5. Every answer is stamped, and the stamp is content-addressed.** An unchanged corpus keeps
its `world_view_id`; two same-day corpora cannot collide; an append-only build history proves
what any given corpus state contained; and the prompt registry proves which prompt version
produced the number.

**6. It measures itself honestly — including where it is weak.** Transcription fidelity and
realized-episode skill are computed and reported *separately and never averaged*; ex-ante
mode date-fences retrieval so forecast skill cannot be inflated by hindsight; and the
resulting abstention rate is published as a statement about corpus depth rather than buried.

**7. Machine and human outputs come from the same evidence.** The cited prose answer and the
structured shock vector are produced in one pass over one retrieved set — the analyst reading
the narrative and the engine consuming the vector are looking at the same basis.

**8. It is testable to the point of being boring.** 803 tests, no GPU, no network, no model
downloads — the entire pipeline runs on a laptop or a plain CI runner behind fakes. That is
what makes rapid, safe change possible in an LLM product.

**9. A model or prompt change is a measured change.** A prompt × model regression matrix with
a saved baseline, an enforced drift gate, and provider portability by contract mean upgrading
a model is an experiment with a number attached, not a leap of faith.

**10. Cost and abuse are controlled, not merely observed.** Hard daily ceilings enforced as
admission control (402), bounded inputs (422), saturation shedding (503) and job conflict
detection (409) — with per-call priced accounting behind all of it.

**11. The engineering discipline is itself demonstrable.** Versioned prompts, doer≠judge
separation, deterministic dev-time hooks, layered agent context files, locked test-first
specs, and an external rubric with a re-audit trigger. This repository is a working reference for how to build an
LLM product under governance, not just an LLM product.

---

# What this system deliberately does not claim

Stating limits precisely is part of the pitch for a risk audience.

- **It is not a snapshot store.** `world_view_id` is a provenance and freshness *stamp*. Once
  re-indexing replaces the live corpus you cannot retrieve or re-synthesize *against* a
  superseded id — the build history proves what that corpus was, it does not let you query it.
  This is deliberate, because the downstream consumer does not replay against superseded
  corpora; the design for an immutable snapshot store is written up should that change.
- **It is not multi-tenant.** A *principal* is an authentication, rate-limit and
  cost-attribution identity behind an API key. It is **not** a data-isolation boundary; the
  corpus is shared. The isolation hook exists but is dormant and partitions nothing today.
- **Forecast skill is bounded by corpus depth.** For historical episodes the ex-ante corpus is
  thin and the system abstains. That is reported as a corpus statement, and it is never merged
  into the headline accuracy.
- **Freshness policy belongs to the consumer.** MacroRAG guarantees a release date exists on
  every cited chunk; whether that date is acceptable against a valuation date is the stress
  engine's rule to enforce.
- **Injection defence is defence-in-depth, not a guarantee.** The deterministic citation gate
  is the hard bound, and the code says so.

---

## Fact-check appendix

| Claim | Where to verify |
|---|---|
| 19-field vocabulary, units, sign conventions | [`src/synthesis/vocab.py`](../src/synthesis/vocab.py) |
| Market-episode cards | [`src/ingest/usstats/episodes.py`](../src/ingest/usstats/episodes.py), `configs/ingest/tier3_usstats.yaml` |
| Citation gate drops ungrounded fields | [`src/synthesis/gate.py`](../src/synthesis/gate.py) |
| Plausibility / anchor / coherence checks | [`src/synthesis/validate.py`](../src/synthesis/validate.py) |
| Per-field drop reasons | [`src/synthesis/diagnostics.py`](../src/synthesis/diagnostics.py) |
| Content-addressed `world_view_id`, build history | [`src/utils/manifest.py`](../src/utils/manifest.py) |
| Per-query trace contents | [`src/utils/trace.py`](../src/utils/trace.py) |
| Prompt registry + provenance | [`src/llm/registry.py`](../src/llm/registry.py), [`prompts/`](../prompts/) |
| Validate → repair → fail-soft | [`src/llm/base.py`](../src/llm/base.py), [`src/llm/schema_health.py`](../src/llm/schema_health.py) |
| Two kinds of shock evidence | [`src/eval/shock_eval.py`](../src/eval/shock_eval.py), [`src/eval/episodes.py`](../src/eval/episodes.py) |
| Self-validation + negative controls | [`src/eval/selfcheck.py`](../src/eval/selfcheck.py) |
| Prompt × model regression matrix | [`src/eval/matrix.py`](../src/eval/matrix.py) |
| Cost ceilings as admission control | [`src/utils/costguard.py`](../src/utils/costguard.py) |
| Injection isolation + detection | [`src/llm/sanitize.py`](../src/llm/sanitize.py) |
| CI gates | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) |
| Dev-time hooks | [`.claude/hooks/`](../.claude/hooks/), [`.claude/settings.json`](../.claude/settings.json) |
| Hardening controls and what is not built | [`docs/design/Macro_RAG_Production_Hardening.md`](design/Macro_RAG_Production_Hardening.md) |
| Test-first spec locks | [`docs/specs/`](specs/), [`.claude/tdd/`](../.claude/tdd/), [`.github/workflows/tdd-spec.yml`](../.github/workflows/tdd-spec.yml) |
| Type-check gate + its scoping rules | [`mypy.ini`](../mypy.ini) |
| Both flows embed through one shared service | [`src/embeddings/__init__.py`](../src/embeddings/__init__.py) (`select_embedder`) |
