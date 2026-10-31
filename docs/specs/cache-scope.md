# cache-scope — cached answers stay within their filter, retrieval options and corpus

## Requirement

`QueryPipeline._answer` has two caches.
- **L1, exact.** Keyed on the normalised query, the filter and a version. The version covers the
  answer prompt, the answer and complex models, the retrieval options and the route.
- **L2, semantic.** Returns any stored result whose query embedding has cosine ≥ the threshold
  with the new query.

Two leaks:
1. **L2 ignores everything but the embedding.** A near-identical question with a different
   `filters` (or principal stamp), or a synthesize call after a plain answer (different retrieval
   options and route), gets another request's chunks. L2 is enabled in
   `infra/config/rag.aws.yaml`.
2. **Neither cache keys on the corpus.** After a re-index, both serve answers from the previous
   corpus until their TTL expires (L1 default one hour; Redis-backed caches survive restarts).
   This breaks the house rule that cache keys change with the corpus, prompt hash and model tier.

Fix: every cached entry carries a **scope**: the filter, the existing version, and the corpus
`world_view_id` read per call. A cache hit requires the same scope.

Out of scope: TTLs, thresholds, eviction; the job, cost and rate-limit stores.

## Acceptance criteria

- **C1**: L2 is scoped. With an in-memory semantic cache and no L1 (`NullCache`):
  - `answer(q, base_filter=A)` then `answer(q, base_filter=B)`: the second is not cached, and its
    chunks honour B;
  - `answer(q, base_filter=A)` twice: the second is cached.
- **C2**: `answer(q)` then `synthesize(q)` on the same pipeline: synthesize's query result is not
  cached.
- **C3**: the corpus is in the scope. Two pipelines share one L1 cache and one semantic cache;
  their manifests differ only in the corpus digest. The same question answered by the first, then
  the second, is **not** cached on the second, for L1 and for L2. With the same digest it is
  cached.
- **C4**: the Redis-backed semantic cache (over `utils.fakeredis.FakeRedis`) behaves as in C1.

Oracle: the `cached` flag and the publishers of the returned chunks, against hand-built corpora.

## Interface

None new. `SemanticCache.get` / `add` may gain a scope argument in the implementation (not under
spec test).

## Notes

- Fixtures: the in-memory pipeline harness (hashing embedder, so identical text gives cosine 1;
  in-memory store; identity reranker; scripted FakeLLM; disabled tracer) over a Federal Reserve
  note and a BIS note. The semantic cache is passed in (`semantic_cache=`); L1 is `NullCache` or
  `InMemoryCache` per test. No network, no `data/`, no clock.
- `world_view_id` comes from the manifest (`utils.manifest.world_view_id`), so the per-call read
  sees a re-index made while the server runs.
- Guide 08's caching section is updated in the implementation PR.
