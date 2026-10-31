# synth-reserve-distinct — reserved slots bring in documents synthesize would not otherwise see

## Requirement

The reserved scenario-study slots (synth-retrieval C1) must widen the evidence: each reserved
slot brings in a scenario-study **document** that the winners would not otherwise contain.

Evidence: on the faultline Taiwan-blockade question (live run after synth-retrieval, corpus
`macro-2026-10-06-feffca9a178f`), all 18 synthesize chunks came from three Taiwan papers
(St. Louis Fed, CSIS, HCSS). Those papers are themselves tagged `cross_asset_scenario`
(`configs/ingest/tier7_scenarios.yaml`), so they already satisfied the "at least R
scenario-study chunks" rule. The reservation was met without adding anything, and the EBA/ESRB
adverse and IMF GFSR geopolitical studies, which the synthesize prompt names as its magnitude
anchors, never reached the model.

Out of scope: retagging the corpus (an ingest and re-index change); the rerank cap
(synth-retrieval C2, unchanged); the `/query` answer path.

## Definitions

Reused from synth-retrieval: *scenario-study chunk*, *reservable* (passes the synthesize
filter), *rerank pool*, *winners* (before neighbour expansion), `R`
(`synthesis.scenario_study_slots`), `K` (retriever `final_k`) and `P` (rerank pool size).

- **Document** of a chunk: its payload `doc_id`.
- **Base winners `W0`:** the winners synthesize would return with `R = 0` and every other
  setting unchanged.
- **New documents `D`:** the documents that have at least one reservable chunk and **no** chunk
  in `W0`.

## Acceptance criteria

- **C1**: In `QueryPipeline.synthesize`, with `r = min(R, |D|, K, P)`:
  - the winners contain chunks from **at least `r` distinct documents in `D`**;
  - those reserved chunks each come from a different document;
  - each reserved chunk was in the rerank pool;
  - the number of winners equals `|W0|`;
  - the winners still include the top `|W0| − r` chunks of `W0`, in `W0`'s ranking: reserved
    chunks replace the lowest-ranked base winners, whatever those are, **including
    scenario-study chunks**.

  A scenario-study chunk already in `W0` does not use up a slot. Exact counts and set
  membership, with neighbour expansion off. Oracle: a hand-built corpus where every `W0` chunk
  is itself a scenario-study chunk (the Taiwan shape). One group of scenario-study documents is
  lexically identical to the query and fills `W0`. A second group is lexically far from it
  and never reaches `W0`. `D` is the second group by construction.
- **C2**: When `D` is empty, synthesize's winners equal `W0`, even with `R > 0`: no base winner
  is displaced and nothing is added. This includes a scenario-study document whose other
  chunks are reservable but outside `W0`: the document is already represented, so it is not in
  `D`. Exact equality of chunk-id lists. Oracle: a corpus whose `W0` mixes non-scenario chunks
  with one chunk from each scenario-study document, and those documents' other chunks are
  lexically far from the query. Every reservable document is then in `W0`, so `D` is empty
  by construction. (Today's code pulls one of those far chunks in and displaces a base winner.)

## Interface

- `pipelines.query_pipeline.QueryPipeline.synthesize(...)` — existing signature, behaviour
  changes (C1, C2). There are no new functions or settings.

## Notes

- Compatible with the graduated synth-retrieval tests by construction: in their corpora, no
  scenario-study chunk is in `W0`, and each study is its own document. So the rule
  "`min(R, S, K, P)` study chunks, replacing the lowest-ranked others" gives the same counts
  there. Those tests must stay green.
- Fixtures are synthetic: hashing embedder, in-memory store, a scripted `FakeLLM` and a
  recording fake reranker (`retriever.rerank.Reranker`). Use `chunk_expansion = False` and
  `mmr_lambda = 1.0`. No `data/`, network or clock.
- Live check after implementation (not a locked test): the Taiwan question's synthesize
  `chunks[]` includes chunks from documents other than the three Taiwan papers, ideally
  `eba_esrb_stress` / `imf_gfsr_geopolitical`.
