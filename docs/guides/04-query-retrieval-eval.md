# 04 — Query, Retrieval & Eval

Stage 3: ask questions against the indexed corpus. This guide covers the `query` CLI,
the retrieval pipeline (what happens between your question and the answer), how to
tune it via `configs/rag.yaml`, and how to measure retrieval quality with `eval`.

> Audience: technical operators / analysts comfortable with the CLI.
> For the no-CLI experience, see [06 – Web UI](06-ui.md) and
> [07 – Business User Guide](07-business-user-guide.md).
> Design background: [pipeline design](../design/Macro_RAG_pipeline_design.md).

---

## 1. Asking questions — `query` and `synthesize`

Two CLI entrypoints ask questions against the indexed corpus: `query` returns a
cited **prose answer** (the user-facing RAG path); `synthesize` additionally emits a
**structured shock vector** (the stress-testing backend). Both hit the same live
Qdrant + LLM configured in `configs/rag.yaml`.

### 1.1 Prose answer — `query`

```
python -m pipelines.cli query "<your question>" [--no-llm]
```

| Flag | Default | Meaning |
|---|---|---|
| `question` (positional) | — | The natural-language question (quote it) |
| `--no-llm` | off | **Retrieval-only** — print ranked chunks, skip LLM generation (no API key needed) |

**Full answer:**

```bash
python -m pipelines.cli query "severely adverse unemployment path in DFAST 2026"
```

prints the cited summary, supporting details with chunk IDs, and any hydrated
time-series. **Retrieval-only** (fast, free, great for debugging recall):

```bash
python -m pipelines.cli query "FOMC dot plot" --no-llm
```

prints the route, extracted filters, and the top ranked chunks with rerank scores.

> Filters: the CLI extracts facet filters (factor/entity tags, date windows) from the
> question text automatically, matching vocabulary cues as whole words (an optional
> plural `s`; "air" does not tag `ai`). To pass explicit filters (tiers, doc types, publishers,
> date ranges), use the REST API ([05](05-api-and-integration.md)) or the UI, which
> accept a structured `filters` object.
>
> In **synthesize**, entity tags extracted from the question do not exclude quantified
> scenario studies (chunks tagged `cross_asset_scenario` / `stress_scenario`, e.g. the
> EBA adverse path or the IMF GFSR geopolitical chapter): they are the magnitude anchors
> the synthesis prompt relies on. All other facets still apply to them, and entity tags
> you pass explicitly in `filters` stay a hard filter.
>
> Synthesize also has its own retrieval knobs in `configs/rag.yaml` → `synthesis`:
> `scenario_study_slots` (default 2) reserves winner slots for those studies so they are
> not outranked by lexically closer prose, and `rerank_candidates` (default 12) caps its
> rerank pool. Rerank on the shared CPU service is roughly linear in the number of
> documents, so this cap is what keeps synthesize inside a 300s consumer timeout. The
> `/query` answer path keeps `retrieval.rerank_candidates`.

### 1.2 Structured shock vector — `synthesize`

For the stress-testing backend, `synthesize` runs the same retrieval + generation
path and additionally emits a **gated, cited shock vector** (the faultline
scenario-synthesis contract): each field is signed, unit-checked against the
controlled field vocabulary, and grounded in ≥1 dated chunk (`direct`) or the closest
historical/official analog (`analog`).

```
python -m pipelines.cli synthesize "<scenario question>" [--horizon-days N] [--scenario "extra narrative"]
```

| Flag | Default | Meaning |
|---|---|---|
| `question` (positional) | — | The scenario / factor-channel question (quote it) |
| `--horizon-days` | none | Pin the scenario horizon (e.g. `180`) |
| `--scenario` | none | Extra scenario narrative folded into both retrieval and synthesis |

```bash
python -m pipelines.cli synthesize "Joint shock vector for a Taiwan invasion tail across rates, credit, equity vol, rates vol and FX" --horizon-days 180
```

prints the retrieval route, the `world_view_id`, grounding **diagnostics**, and each
gated shock field as `NAME value unit [grounding_mode] cites=[chunk_ids]`, followed by
the prose answer. Fields with too few dated citations are **gated out rather than
invented** — an empty vector means the corpus doesn't quantify that scenario (surfaced
as a coverage gap, not a fabricated number). The same surface is available over REST
(`POST /query` with `mode: "synthesize"`) and MCP (`macro_synthesize_shock_vector`) —
see [05](05-api-and-integration.md). Field names, units and sign conventions are the
shock-field schema in `src/synthesis/vocab.py` (also exposed at `GET /synthesis/schema`).

---

## 2. What happens during retrieval

Understanding the stages helps you tune and troubleshoot:

1. **Analyze** — classify the query into a route (`DIRECT`, `PARAPHRASE`,
   `DECOMPOSE`, `DECOMPOSE_AND_PARAPHRASE`, `HYDE`; `PLAN_EXECUTE` is assigned by the
   quantitative-agent gate) and extract facet filters
   from the controlled vocabulary (factor/entity tags, year windows).
2. **Hybrid search** — bge-m3 dense + sparse vectors query Qdrant per branch
   (paraphrase / sub-queries / HyDE), with facet filters applied at search time.
3. **RRF fusion** — Reciprocal Rank Fusion merges branch result lists.
4. **Rerank** — `bge-reranker-v2-m3` cross-encoder re-scores the pooled candidates
   down to `final_k` (default 8).
5. **Relevance-floor gate** — if the best rerank score is below `min_rerank_score`,
   the system can **refuse** rather than answer from weak evidence (off by default).
6. **Time-decay** (optional) — boost recent releases, modulated by bucket (A/B vs C).
7. **MMR** (optional) — diversify near-duplicate winners.
8. **Neighbour expansion** — pull adjacent chunks for context.
9. **Hydrate time-series** — attach matching exact numbers from the SQLite store.
10. **Generate + verify** — produce a cited answer and run a faithfulness check that
    drops unsupported claims. Complex/quantitative queries route through a typed
    **plan-execute agent** with a safe calculator. The agent's chunks are cut to the
    caller's `filters` (its own retrieval is unfiltered). `mode: "synthesize"` never takes
    this route, so its filters and the reserved scenario-study slots (which bring
    market-episode cards in) always apply (spec `synth-route-filters`).

> **Prompt-injection defense.** Retrieved chunk text is fenced as **untrusted data** in
> the generation prompt — it is never followed as instructions
> ([Production Hardening §D](../design/Macro_RAG_Production_Hardening.md)).

---

## 3. Tuning retrieval (`configs/rag.yaml`)

All knobs have sensible code defaults; the committed `configs/rag.yaml` already
overrides several (notably `rerank_candidates: 24` — see §CPU-latency note below).
Edit it to tune further. The most useful knobs (code-default values shown):

```yaml
retrieval:
  final_k: 8               # chunks handed to the generator
  rerank_candidates: 60    # pool size fed to the reranker (rag.yaml ships 24 on CPU)
  use_hyde: true           # hypothetical-document expansion
  use_decompose: true      # split multi-part questions
  use_router: true         # LLM route classification
  mmr_lambda: 1.0          # 1.0 = pure relevance; <1.0 diversifies
  chunk_expansion: true    # pull prev/next neighbours
  hydrate_timeseries: true # attach matching numeric series
  plan_execute: true       # typed plan + calculator for quantitative queries
  min_rerank_score: 0.0    # relevance floor; raise (e.g. 0.3) to refuse weak matches
  verify_faithfulness: true

reranker:
  enabled: true            # disable for a faster, lower-quality path

ranking:
  time_decay: false        # enable to favour recent releases
  half_life_days: 180
  bucket_decay: { A: 1.0, B: 1.0, C: 0.5 }
```

Practical tuning recipes:

| You want… | Change |
|---|---|
| Fewer hallucinations / "I don't know" on thin evidence | Raise `min_rerank_score` (e.g. 0.3); keep `verify_faithfulness: true` |
| More diverse evidence (less redundancy) | Set `mmr_lambda` to ~0.7 |
| Latest releases to win ties | `ranking.time_decay: true` |
| Faster/cheaper retrieval | `reranker.enabled: false`, `use_hyde: false`, lower `rerank_candidates` |
| Better multi-part / quantitative answers | Keep `use_decompose` and `plan_execute` on |

> **CPU-only performance.** With no CUDA GPU, `bge-reranker-v2-m3` scoring the full
> 60-candidate pool dominates latency — a cold, uncached query can take **~150 s**,
> which will time out most UI/HTTP clients. On a CPU box, trim the rerank work:
>
> ```yaml
> retrieval:
>   rerank_candidates: 24   # what rag.yaml ships (code default 60) — ~3x less reranker compute
>   use_hyde: false         # one fewer search branch -> fewer chunks to rerank
> ```
>
> Also raise the client/server request timeout (~120 s) to absorb the first-query
> model load. The cache hides this for repeat queries, but the **first** call after a
> restart always pays it. For a sub-second path at some quality cost, set
> `reranker.enabled: false`. A small GPU is not enough — bge-m3 + bge-reranker need
> more than ~2 GB VRAM to co-reside.

After changing models or chunking, **re-index** ([03](03-indexing.md)).

---

## 4. LLM provider & tiered routing

*(Canonical LLM-config reference — other docs point here.)*

Generation uses a cheap model for plan/verify and a frontier model for complex
routes. The model tiers live in a **per-provider `profiles:` block**, so selecting
`provider` remaps every tier at once (`configs/rag.yaml`):

```yaml
llm:
  provider: openai            # or anthropic — the active profile below supplies the models
  profiles:
    openai:
      plan:    gpt-5.4-mini   # planner / router / decompose / verify — keep capable
      answer:  gpt-5.4-nano   # simple-query generation (cheap)
      complex: gpt-5.4        # complex routes incl. PLAN_EXECUTE generation + synthesis
      default: gpt-5.4-nano
    anthropic:
      plan:    claude-sonnet-4-6
      answer:  claude-haiku-4-5
      complex: claude-opus-4-8
      default: claude-haiku-4-5
  use_replacement_models: false   # feature switch, see below
  replacement_profiles:
    openai:
      plan:    gpt-6-sol
      answer:  gpt-6-luna
      complex: gpt-6-sol
      default: gpt-6-luna
    anthropic:
      plan:    claude-sonnet-5-5
      answer:  claude-haiku-5-5
      complex: claude-opus-5-5
      default: claude-haiku-5-5
  complex_routes: [DECOMPOSE, DECOMPOSE_AND_PARAPHRASE, PLAN_EXECUTE]
```

> **Replacement models.** OpenAI shuts down `gpt-5.4-nano` on 2027-04-01 and names
> `gpt-6-luna` as its replacement. The Anthropic profile predates the Claude 5 family.
> `replacement_profiles` holds the next set: `gpt-6-sol` for plan and complex and `gpt-6-luna`
> for answer; the Claude 5.5 models for Anthropic. `use_replacement_models: true` takes the
> active provider's tiers from it; a provider with no replacement profile keeps its profile. To try the set for one run without editing config, set
> `MACRORAG_USE_REPLACEMENT_MODELS=1` (`0`/`false`/`no`/`off` forces it off; any other value
> raises). Flat tier keys still win. Compare the two sets with
> `eval-matrix --models ...` or `eval --judge` before turning it on.

> **Do not set the `plan` tier to
> `gpt-5.4-nano`** — it drives the PLAN_EXECUTE planner and nano plans poorly enough
> to break retrieval (returns the wrong documents). Use `gpt-5.4-mini` (validated: it
> invokes the structured time-series lookup correctly, ~3.3× cheaper than `gpt-5.4`);
> the `answer` tier can be nano. `gpt-4o-mini` / `gpt-4o` are legacy and only work if
> set explicitly.
>
> **Overriding a single tier:** a flat `plan_model`/`answer_model`/`complex_model` key
> still works and *wins over* the active profile — use it to pin one tier without
> editing the profile. GPT-5.x requires `max_completion_tokens` (the OpenAI client sends
> the right param); Opus 4.7 and later and the Claude 5 family reject `temperature` (the
> Anthropic client omits it automatically).
>
> **Every model needs a price.** Any model id `configs/rag.yaml` can reach (every profile and
> replacement profile of every provider, flat tier keys, `escalation`) must have a row in
> `configs/llm_pricing.yaml`, or settings refuse to load with a `ValueError` naming it;
> `eval-matrix --models` and `--judge-model` are checked the same way. Add the row first,
> since an unpriced call would count as $0 against the cost cap.

**Per-tier output budget** (`tier_options`, optional, per profile). Each tier can set its own
output cap, reasoning effort and HTTP timeout; anything unset keeps `llm.max_tokens`, the
provider's default effort and `llm.resilience.request_timeout_s`:

```yaml
    anthropic:            # inside replacement_profiles (or profiles)
      plan:    claude-sonnet-5-5
      ...
      tier_options:
        plan:    {max_tokens: 6000, effort: low}
        complex: {max_tokens: 12000, effort: high, timeout_s: 180}
```

- **Why it matters:** reasoning and thinking tokens count against the cap. The Claude 5 models
  think by default, and the gpt-6 models reason by default where gpt-5.4 does not, so a cap
  that fits gpt-5.4 can cut their replies off. A cut-off shows up as `truncated` in
  `GET /admin/schema-health`; raise that tier's `max_tokens` or lower its `effort`.
- **Effort values:** Anthropic `low`, `medium`, `high`, `xhigh`, `max`; OpenAI `none`, `low`,
  `medium`, `high`, `xhigh`, `max`. Anything else fails at load. Not every model takes every
  level (Sonnet 4.6 has no `xhigh`; Haiku 4.5 takes no effort), so try a new setting with
  `eval-matrix` first. On OpenAI, an effort above `none` drops `temperature` from the request,
  because gpt-5.4 rejects it with reasoning on.
- Tier names are `plan`, `answer`, `complex`, `default` and `escalation`; a typo fails at load.
  Changing a tier's options invalidates cached answers.
- Keep `max_tokens` under about 21K: requests are not streamed.

**Prompts are versioned separately** from models: each task's template lives in
`prompts/tasks/<task>/base.md` (with a `schema.json` the JSON output is validated
against, plus a one-shot repair retry) and is resolved by `src/llm/registry.py`, which
stamps every generation with a `variant_id` + `content_hash` into the trace.

**Quality-signal escalation** (`escalation:` in `configs/rag.yaml`, **off by default**):
when the faithfulness verifier drops too many claims or the citation gate loses too many
shock fields, the pipeline retries the generation on a higher tier and keeps it only if
better; each upgrade is logged to the trace `escalations[]`. Set an `escalation` tier in
the profile to enable it for synthesis / complex routes (with the default profiles,
`complex` is already the top tier, so only simple-route prose escalates). Full design:
[Macro_RAG_LLM_Harness.md](../design/Macro_RAG_LLM_Harness.md).

**Resilience** (`llm.resilience.*` in `configs/rag.yaml`): transient LLM failures (429 /
5xx / timeout) are retried automatically with exponential backoff + jitter behind a
circuit breaker, and every call carries an explicit timeout — so a flaky provider
degrades gracefully instead of erroring the request
([Production Hardening §H](../design/Macro_RAG_Production_Hardening.md)).

Run `--no-llm` to skip generation entirely when you only care about retrieval.

---

## 5. Evaluating retrieval quality

```
python -m pipelines.cli eval [--k 8] [--golden <path>] [--shock [<ref.yaml>]] [--judge] [--fail-on-regression [--tol N]]
```

| Flag | Default | Meaning |
|---|---|---|
| `--k` | 8 | Cutoff for `hit@k` / `recall@k` |
| `--golden` | `configs/eval/golden.yaml` | Golden set to score (point at any golden YAML) |
| `--shock` | `configs/eval/shock_reference.yaml` | Switch to **shock-accuracy** mode: score synthesized vectors against *projected* official scenario paths (coverage / sign-accuracy / tolerance). This is **transcription fidelity, not forecast skill** — see the two-kinds-of-evidence note below |
| `--judge` | off | Also run the LLM **judge** (config `eval.judge`) and fold mean faithfulness/relevancy into the summary — needs a provider key ([Production Hardening §B](../design/Macro_RAG_Production_Hardening.md)) |
| `--fail-on-regression` / `--tol` | off / 0.05 | **CI gate** — exit 1 if the summary regresses beyond `--tol` vs the saved baseline ([Production Hardening §K](../design/Macro_RAG_Production_Hardening.md)) |

It runs the **golden set** through retrieval and reports `hit@k`, `recall@k` and `MRR`
per question plus an aggregate summary. Eval is **retrieval-only** (no LLM / no API key
needed) — it builds the retriever with the heuristic analyzer and scores where the
gold sources land in the ranking:

```bash
python -m pipelines.cli eval --k 8
python -m pipelines.cli eval --k 8 --golden configs/eval/golden_user_facing.yaml
```

```
=== retrieval eval ===
  hit@8=1 mrr=0.50 | What is the 2026 severely adverse unemployment...
summary: {'hit@8': 0.875, 'recall@8': 0.92, 'mrr': 0.65}
```

### Prompt × model regression matrix (`eval-matrix`)

Runs each `(prompt variant, model)` cell over a golden set, scores it (answer:
faithfulness/relevancy via the LLM judge; synthesize: field coverage/grounding), and
diffs against a stored baseline — so a prompt edit or a model bump can't silently
regress quality. Unlike `eval`, this drives the LLM, so it needs a provider key.

```bash
python -m pipelines.cli eval-matrix --task synthesize \
  --models gpt-5.4,gpt-5.4-mini --variants latest \
  --golden configs/eval/golden_stress_backend.yaml --save-baseline
python -m pipelines.cli eval-matrix --task answer --fail-on-regression   # CI gate
```

| Flag | Meaning |
|---|---|
| `--task` | `answer` or `synthesize` (required) |
| `--models` | comma list (default: the profile's `answer` + `complex` tiers) |
| `--variants` | comma list of prompt `variant_id`s to compare (default: `latest`) |
| `--baseline` / `--save-baseline` | baseline JSON to diff against / to (re)write it |
| `--fail-on-regression` / `--tol` | exit non-zero on a regression beyond `--tol` (default 0.05) |

Full design: [Macro_RAG_LLM_Harness.md](../design/Macro_RAG_LLM_Harness.md) §7.

### Bundled golden sets

| File | Questions | Focus |
|---|---|---|
| `configs/eval/golden.yaml` | 8 | Smoke set across the tiers (the CLI default) |
| `configs/eval/golden_user_facing.yaml` | 50 | User-facing RAG / UI questions (medium→complex, multi-source) |
| `configs/eval/golden_stress_backend.yaml` | 50 | Stress-testing backend / shock-vector questions |

The two 50-question sets are derived from the analyst question banks in
`scripts/questions_user_facing_rag.md` and `scripts/questions_stress_testing_backend.md`.
The backend set carries extra free-text stub keys (`mode`, `expected_fields`,
`grounding`, `expected_behavior`) that document the shock-vector facets a correct
answer must produce; the retrieval harness **ignores unknown keys**, so those items
still run and are retrieval-scored on their grounding sources.

### Golden set format

```yaml
questions:
  - question: "What is the 2026 severely adverse unemployment rate path in the Fed stress test?"
    match_field: source_id        # which payload field to match against
    expected: ["dfast"]           # expected values for that field
    # filter: { ... }             # optional seed filters
```

`match_field` may target a **scalar** payload field (`source_id`, `doc_type`) or a
**list-valued** one (`factor_tags`, `entity_tags`) — a chunk matches when its tag-set
intersects `expected`, so multi-value `expected` + `recall@k` measures how many of the
required grounding sources/factors surfaced. Valid values come from the closed
vocabulary in `configs/ingest/vocabulary.yaml` (factor/entity tags, doc types) and the
endpoint `id:`s in `configs/ingest/tier*.yaml` (source ids).

### Running the questions live (end-to-end, real LLM)

`eval` scores retrieval only. To run a golden file's questions **end-to-end** against
live Qdrant + the LLM (prose answers or gated shock vectors), use the batch runner:

```bash
# user-facing prose answers
python scripts/run_questions_live.py --file configs/eval/golden_user_facing.yaml --mode answer --limit 2
# backend shock vectors
python scripts/run_questions_live.py --file configs/eval/golden_stress_backend.yaml --mode synthesize --limit 2 --horizon-days 180
# a single ad-hoc question
python scripts/run_questions_live.py --question "Outlook for the term premium?" --mode answer
```

It builds the pipeline once (models load a single time) and prints the route, retrieved
`source_id` distribution, the answer, and — in `synthesize` mode — the gated shock
vector with grounding diagnostics.

### Shock accuracy: two kinds of evidence that must never be averaged

`eval --shock` is only one of the two, and conflating them would overstate the system.
Keep them apart when reporting:

| Evidence | What it scores against | What it actually measures | Where |
|---|---|---|---|
| **Transcription fidelity** | *Projected* official paths — the DFAST/EBA baseline→adverse delta | Did the system read the Fed's published scenario and transcribe it correctly? | `eval --shock` (CLI) · `src/eval/shock_eval.py` |
| **Realized-episode backtest** | What markets *actually did*, derived from the ingested `historic` series (1976-Q1 → 2025-Q4, 28 variables) | Forecast-shaped skill | `POST /admin/backtest` · `src/eval/episodes.py` |

The episode backtest runs in **two separately-named modes** whose metrics are
structurally prevented from merging (summary keys are mode-prefixed):

- **`grounding_fidelity`** — evidence visible. *Given the relevant research, does the system
  turn a described scenario into the right numbers?*
- **`ex_ante_skill`** — retrieval hard-filtered to `release_date` **strictly before** the
  episode opens, so only documents that existed at the time are visible. This is genuine
  forecast skill and it is **bounded by corpus depth, not model quality** — the corpus is
  mostly recent, so for older episodes there is little to retrieve and the system correctly
  abstains. Report `ex_ante_skill_abstention_rate` *alongside* accuracy as a statement about
  the corpus; never fold it in.

### Self-validation (no judge, no labelling) — `POST /admin/selfcheck`

The time-series store is populated by deterministic parsers at ingest, while the answer path
runs embed → retrieve → rerank → generate. Those are **independent code paths over the same
documents**, so the store is a machine-checkable answer key. Each stored
`(variable, scenario, period, value)` row templates a question whose answer is already known,
and each case is scored on two independent signals — did hydration surface the right row, and
does the prose state the right number — which localises a failure to retrieval, generation, or
the store. Alongside it run **negative controls**: probes for variables and scenarios that do
not exist, plus false-premise questions, classified four ways rather than pass/fail (correcting
the premise while naming a real number is the *best* outcome, so collapsing it into a failure
would punish it). Only `fabricated` counts against the system. Deterministic, no LLM judge.

> **Both surfaces are admin-API only — there is no CLI for them.** Use
> `POST /admin/selfcheck`, `GET /admin/backtest/episodes` (list the catalog) and
> `POST /admin/backtest`; each returns a job you track via `/admin/jobs`
> ([05 §4](05-api-and-integration.md)). `src/eval/{selfcheck,episodes}.py` are the code.

### Drift gate

`eval` can compare the summary against a saved baseline
(`data/eval/baseline.json`) to catch regressions after re-indexing or config changes.
Save/update the baseline and view drift via the admin API
(`GET/POST /admin/eval/baseline`, see [05](05-api-and-integration.md)). The CLI
`eval --fail-on-regression [--tol]` exits 1 on a regression (a CI gate); the admin
`POST /admin/eval` job can likewise alert or fail on regression via
`eval.fail_on_regression` / `eval.alert_on_regression`
([Production Hardening §K](../design/Macro_RAG_Production_Hardening.md)).

### Growing the golden set from real usage

User thumbs-up/down feedback (`POST /feedback`, the UI, or `GET /admin/feedback`) is
logged to `data/traces/feedback.jsonl`. `POST /admin/feedback/reconcile` turns positive,
chunk-citing feedback into **deduped candidate** golden entries written to a review file —
never auto-promoted; a human curates them into the golden set
([05 §4](05-api-and-integration.md); [Production Hardening §B](../design/Macro_RAG_Production_Hardening.md)).

---

## 6. Tracing & explainability

Every query is traced to `data/traces/queries.jsonl` (route, candidate scores,
timings). Inspect via `GET /admin/traces` or the UI's **Traces** screen (waterfall,
score evolution, evidence drill-down). This is the first place to look when an answer
is wrong or surprising. Suspected prompt-injection markers in the retrieved context are
stamped on the trace as `security.injection_markers` and counted in Prometheus
(`macrorag_prompt_injection_suspected_total`) ([Production Hardening §D](../design/Macro_RAG_Production_Hardening.md)).

---

## 7. Troubleshooting (query / retrieval / eval)

| Symptom | Likely cause | Fix |
|---|---|---|
| "I don't have enough evidence" / refusal | `min_rerank_score` too high, or corpus lacks the doc | Lower the floor; ingest the missing source; check `--no-llm` recall |
| Answer cites wrong/old document | Time-decay off, or stale index | Enable `ranking.time_decay`; re-index after re-ingest |
| Relevant doc never retrieved | Over-aggressive facet filters | Inspect route+filters with `--no-llm`; the system retries unfiltered if filters starve recall — confirm the doc is indexed |
| Hallucinated numbers | LLM guessing instead of using series | Keep `hydrate_timeseries: true` and `plan_execute: true`; verify the time-series store has the data |
| Claims dropped from the answer | Faithfulness verifier removed unsupported text | Expected behaviour; check `unsupported` list; improve retrieval if too aggressive |
| Eval `hit@k` dropped after a change | Retrieval regression | Compare against baseline; revert the config/model change or re-tune |
| Query very slow | Reranker on CPU, large `rerank_candidates`, HyDE branches | Use GPU; lower `rerank_candidates`; disable `use_hyde` |
| LLM 401/429 | Bad/missing key or rate limit | Transient 429/5xx/timeout are retried automatically (`llm.resilience.*`, [§H](../design/Macro_RAG_Production_Hardening.md)); a persistent 401 means a bad key — check `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`; or `--no-llm` |

Next: serve this over HTTP and integrate other systems → [05 – REST API & Integration](05-api-and-integration.md).
