# synth-retrieval — reserved scenario-study slots and a cheaper rerank for synthesize

## Requirement

Synthesize must reliably put the corpus's quantified scenario studies in front of the model,
and its retrieval must finish well inside a consumer's 300s timeout.

Evidence:

- **Slots.** After synth-grounding, the EBA/ESRB adverse and IMF GFSR geopolitical studies are
  *eligible* for the faultline Taiwan-blockade question, but a live re-run (2026-10-11,
  corpus `macro-2026-10-06-feffca9a178f`) still drew all 20 final chunks from three Taiwan
  papers. Eligible is not enough: lexically closer prose outranks the studies.
- **Latency.** Live stage timings for the same question show:
  - batched embedding of 6 sub-queries: ≈3–6s, the same as embedding 1;
  - the vector search: 0.1–0.6s;
  - rerank: ≈8s **per document**, linear in the count (6 docs → 64s, 12 → 101s, 24 → 198s).

  Truncating the text sent to the reranker saved little until it changed the ranking
  (1,500 chars: −17%; 800 chars: −59% but 2 of the top 6 changed). The number of documents
  reranked is the lever. Branch count is not, so it is out of scope.

Out of scope: the reranker model and throughput, which belong to RagInference
(`jina-reranker-v3`, ≈4× slower than the bge-reranker-v2-m3 this repo's `rerank_candidates: 24`
was tuned for), and the `/query` answer path, which is unchanged.

## Definitions

- **Scenario study chunk:** a chunk whose `factor_tags` include any of
  `synthesis.vocab.SCENARIO_STUDY_TAGS` (`cross_asset_scenario`, `stress_scenario`).
- **Synthesize filter:** the filter synthesize already retrieves with (synth-grounding C2).
  A scenario-study chunk is *reservable* only if it passes that filter, so caller-supplied
  `entity_tags` and every other facet still apply to it.
- **Rerank pool:** the documents passed to the reranker in one synthesize call.
- **Winners:** the chunks the retriever returns before neighbour expansion.

## Acceptance criteria

- **C1**: Let `R = settings.synthesis.scenario_study_slots` (new; default `2`; `0` disables
  the reservation), `K` = the retriever's `final_k`, `P` = the rerank pool size (C2), and `S` =
  the number of reservable scenario-study chunks in the corpus. In `QueryPipeline.synthesize`:
  - the winners contain **at least** `min(R, S, K, P)` scenario-study chunks, even when every
    one of them would otherwise rank below the top `K`;
  - the number of winners equals what it would be with `R = 0`. Reserved chunks replace the
    lowest-ranked other winners; they are not added on top;
  - each reserved chunk is in the rerank pool;
  - with `R = 0`, the winners and the rerank pool are the ones synth-grounding already gives.

  Exact counts and chunk-id membership, with neighbour expansion off. Oracle: a hand-built
  corpus in which every scenario-study chunk is lexically far from the query and every other
  chunk is lexically close. Without the reservation, no scenario-study chunk reaches the top
  `K`, so the expected counts follow from the rule above by inspection.
- **C2**: Let `N = settings.synthesis.rerank_candidates` (new; default `12`; `None` means use
  the retriever's own `rerank_candidates`) and `F` = the number of distinct fused candidates
  available (branch results plus the C1 scenario-study search). In `QueryPipeline.synthesize`,
  the reranker is called **once**, on exactly `min(N, F)` documents. The C1-reserved chunks are
  counted inside `N`, not on top of it. `QueryPipeline.answer` still reranks
  `min(retriever rerank_candidates, its own fused count)` documents, unchanged by `N`. Exact
  counts. Oracle: a recording fake reranker (an I/O-boundary fake implementing
  `retriever.rerank.Reranker`, which wraps the identity ordering and records each call's
  document count), with a corpus sized so that `F` is known by construction.

## Interface

- `utils.config.SynthesisSettings.scenario_study_slots: int = 2` — new field.
- `utils.config.SynthesisSettings.rerank_candidates: int | None = 12` — new field.
- `pipelines.query_pipeline.QueryPipeline.synthesize(...)` — existing signature, behaviour
  changes (C1, C2).

## Notes

- All fixtures are synthetic: hashing embedder, in-memory store, a scripted `FakeLLM`, and the
  recording fake reranker described in C2. No `data/`, no network, no current date.
- Spec tests set the new fields directly (`settings.synthesis.scenario_study_slots = …`).
  Neighbour expansion is turned off (`chunk_expansion = False`) wherever winners are counted.
- The default `12` was chosen by the human: about 100s of rerank instead of about 200s at the
  measured rate, and still 1.5× `final_k`. The recall cost is measured during implementation on
  `configs/eval/golden_stress_backend.yaml` (retrieval-only, synthesize filter, 12 vs 24) and
  reported on the implementation PR. It is not a locked test.
- Live check after implementation (not a locked test): the Taiwan question's synthesize
  `chunks[]` contains EBA/GFSR chunks, and retrieval takes about 110s or less.
