# Harness re-audit

<!-- reaudit:last=2026-10-17 -->
<!-- reaudit:next_due=2027-01-15 -->
<!-- reaudit:cadence_days=90 -->

Every harness component encodes an assumption about what the model can't do on its own: a
model tier, an escalation ladder, a verifier, a decomposer. Those assumptions go stale as
models improve. This file holds the re-audit dates, the current scores against the
AI-harness-engineering standard, and the register of components with the evidence for
keeping each one. [`scripts/harness/check_reaudit.py`](../../scripts/harness/check_reaudit.py)
reads the markers above and reports `OVERDUE` once `next_due` passes.

## Cadence

- **Event trigger:** re-audit within 2 weeks of a model change in `configs/rag.yaml`
  (`llm.profiles`, `llm.use_replacement_models`, a flat `*_model` override, or `provider`) and
  of a major model release from a provider we use. The `gpt-5.4-nano` shutdown on 2027-04-01
  forces a model change before that date.
- **Periodic floor:** at least every **90 days**.
- **Standard:** the AI-harness-engineering skill's dated standard, currently **rev 2026-10**
  (ten dimensions). Re-audit when the standard gets a new revision.

| | Date |
|---|---|
| Last re-audit | **2026-10-17** (rev 2026-10) |
| Next re-audit due | **2027-01-15**, or within 2 weeks of a model change, whichever is first |

## Dimension scores (rev 2026-10)

| # | Dimension | Score | Evidence | Top gap |
|---|---|---|---|---|
| 1 | Context & memory | Partial | Module `CLAUDE.md` files (`src/ingest`, `src/llm`, `ui`), `.claude/rules/rag.md`, documentation map with one owner per topic | Root `CLAUDE.md` is 317 lines against a ~200-line target |
| 2 | Planning | Solid | `tdd-spec` flow: `docs/specs/<id>.md`, locked spec tests, `tdd-spec.yml` + `tdd-path-guard.yml` | No scope document an agent declines against |
| 3 | Execution | Partial | Built-in subagents only; no project subagents | None needed so far; add one only after an observed failure |
| 4 | Evaluation | Solid | Golden sets, `eval --fail-on-regression`, LLM judge, `eval-matrix`, `selfcheck`, realized-episode backtest, Stop gate, `review-core` lens pack | Run-to-run spread of the eval scores is not measured |
| 5 | Guardrails | Solid | [`guard_protected_paths.py`](../../.claude/hooks/guard_protected_paths.py) (fails closed, symlink-aware, file and shell tools), ruff fixer, Stop gate; both-ways tests in [`test/unit/test_hooks.py`](../../test/unit/test_hooks.py); ruff + mypy + tests in CI | The launcher is `python` on PATH: if it is missing, the hook errors without blocking |
| 6 | Containment | Partial | Read-only GitHub token, passphrase-gated push, `.env` guard | Native Windows has no Bash sandbox; `.env` lives in the agent's workspace; a broad `Bash(python -)` allow in the personal `settings.local.json` |
| 7 | Prompt & tool hygiene | Solid | Prompt registry (`prompts/`, `prompt_hash`), `base.py` interfaces with fakes, `complete_json` schema validation, `sanitize` / `fence_untrusted` | — |
| 8 | Cost | Solid | `record_call` pricing from `configs/llm_pricing.yaml`, `CostGuard` (402 hard cap), escalation `cost_budget_usd`, circuit breaker, `cost-report` skill | Prompt-cache hit rate is 0.3% of prompt tokens |
| 9 | Autonomy safety | Partial | Git workflow, query traces, job cancel, alerting | No written policy for headless or scheduled agent runs |
| 10 | Evolution & measurement | Partial | This file and `check_reaudit.py` | Hook blocks and skill use are not measured |

## Component register

Each row is a harness component and the assumption it encodes. On a re-audit, test with
evidence whether the current model still needs it. Evidence comes from
`data/traces/queries.jsonl` (225 traces, 2026-06-24 to 2026-10-16, $16.15 of LLM spend).

| Component | Assumption ("the model can't…") | Retire when | Evidence (2026-10-17) | Verdict |
|---|---|---|---|---|
| Model tiers (`llm.profiles`: plan `gpt-5.4-mini`, answer `gpt-5.4-nano`, complex `gpt-5.4`) | …plan retrieval well on the cheapest tier | The nano tier plans as well as mini on the golden set | Documented failure: nano returns IMF chunks instead of the DFAST data | Keep |
| Replacement model set (`llm.replacement_profiles` behind `llm.use_replacement_models`, off): OpenAI `gpt-6-sol` / `gpt-6-luna`, Anthropic Claude 5.5 | — (the next models, not yet in use) | Switched on, or replaced by a newer set | `gpt-5.4-nano` shuts down 2027-04-01 (replacement `gpt-6-luna`); the Anthropic profile (`claude-sonnet-4-6`, `claude-haiku-4-5`, `claude-opus-4-8`) predates the Claude 5 family. All five new models priced, and answered smoke calls on 2026-10-17 | **Decide** by eval runs (`eval-matrix --models`, `eval --judge`) before 2027-04-01 |
| Quality-signal escalation (`llm/escalation.py`) | …a cheap tier won't deteriorate | It never fires when enabled | Disabled (`escalation.enabled: false`); 0 escalations in 225 traces | **Removal candidate**: enable it on an eval run to measure, or delete it |
| Faithfulness verifier (`llm/verify.py`) | …the generator won't make unsupported claims | Unsupported-claim rate ≈ 0 | 45 of 225 answers (20%) had at least one unsupported detail; 62 of 740 details (8.4%) | Keep |
| Query router and decomposition (`retriever/analysis.py`) | …answer compound macro questions without splitting them | A direct route matches decomposition on the golden set | Of 137 routed traces: DECOMPOSE 66, PLAN_EXECUTE 44, DIRECT 17, HYDE 6, PARAPHRASE 2, MULTI_HOP 2 | Keep; test a no-decompose eval slice |
| Multi-hop decomposition (`retriever/multihop.py`) | …answer causal chains in one hop | — | Off (`multi_hop_max_hops: 0`); 2 traces | **Removal candidate**: measure on the stress golden set or delete |
| Synthesis citation gate + validation (`synthesis/gate.py`, `validate.py`) | …emit only grounded, plausible shock fields | Never: it is faultline's contract | — | Keep |
| Untrusted-text fencing (`llm/sanitize.py`) | …resist injected instructions in retrieved text | Never: security control | — | Keep |
| Resilience retry and breaker (`llm/resilience.py`) | …provider calls never fail | Never: environmental | — | Keep |
| Protected-path guard | …an agent won't touch secret files | Never: security control | Fails closed, tested both ways | Keep |
| Stop gate + ruff fixer + standards ratchet | …a generator won't stop on a red build | Never: process guarantee | Tested in `test/unit/test_hooks.py` | Keep |

Rule of thumb: capability scaffolding (tiers, escalation, decomposition, multi-hop) is a
removal candidate as models improve; process and security guarantees (citation gate, Stop
gate, guard, fencing) stay whatever the model.

## Procedure

1. **Gather evidence** for every register row: the query traces (escalations, verifier
   drops, route mix, cost, cached tokens), the latest `eval` and `eval-matrix` runs, and
   the providers' pricing and deprecation pages.
2. **Score the ten dimensions** against the current standard and update the table.
3. **Decide each register row** (keep, test, retire) from the evidence. Remove one
   component at a time, each checked with an eval run.
4. **Re-verify prices and model lifecycles** for every id in `llm.profiles` and
   `configs/llm_pricing.yaml`.
5. **Update the markers:** `reaudit:last` to today, `reaudit:next_due` to today plus
   `cadence_days`, and the dates table.
6. Run `python scripts/harness/check_reaudit.py`; it should print `OK`.
