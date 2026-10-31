# answer-cache-bypass — no cached refusals, and a per-request cache bypass

## Requirement

`QueryPipeline._answer` keeps two answer caches: L1 (exact key) and L2 (semantic, near-identical
query embedding) ([cache-scope](cache-scope.md), retired). Synthesize goes through the same path for
its retrieval and prose answer; the shock vector itself is never cached.

Two gaps:

1. **L1 stores refusals.** L2 already skips a refused answer (`answer.refused`), but L1 stores it,
   so a refusal is served again until the entry expires (one hour by default, longer for a
   Redis-backed L1).
2. **No caller can skip the caches.** faultline's stability measure resolves each scenario three
   times and reports the spread. With the caches on, the three runs share one retrieval, so the
   spread only reflects the synthesis call.

When this change is done:
- neither cache stores a refused result;
- a request can set `no_cache: true`. Then neither cache is read and the fresh result is written to
  neither. This works for the pipeline (`answer`, `synthesize`), REST (`POST /query` in both modes,
  `POST /query/stream`) and MCP (`macro_query`, `macro_synthesize_shock_vector`).

The default (`no_cache` false) is unchanged except for item 1.

Out of scope: cache keys and scoping (cache-scope), cache TTLs, caching shock vectors, the factor
and other endpoints.

## Definitions

- **Cached**: `QueryResult.cached`, and the `cached` key of the `/query`, `/query/stream` and MCP
  responses (in synthesize mode, the key of the embedded query result).
- **Refused**: `QueryResult.answer.refused` is true. In the fixture pipeline, a refusal is forced by
  setting `settings.retrieval.min_rerank_score` above every chunk's score.

## Acceptance criteria

- **C1**: neither cache stores a refused result. With an in-memory L1 (`utils.cache.InMemoryCache`)
  and no L2, `answer(q)` twice where both calls refuse: the second result is refused and has
  `cached` false. The same holds with no L1 (`NullCache`) and an in-memory semantic cache. With the
  floor at its default (no refusal), the second call has `cached` true, as today. Exact. Oracle:
  this requirement.
- **C2**: `QueryPipeline.answer(query, *, base_filter=None, retrieve_opts=None, use_agent=True,
  no_cache=False)`. With an in-memory L1 and an in-memory L2:
  - **not read**: `answer(q)` then `answer(q, no_cache=True)` → the second has `cached` false;
  - **not written**: `answer(q, no_cache=True)` then `answer(q)` → the second has `cached` false,
    and a third `answer(q)` has `cached` true;
  - `no_cache=False` behaves exactly as omitting it.

  Exact. Oracle: this requirement.
- **C3**: `QueryPipeline.synthesize(query, *, base_filter=None, fields=None, horizon_days=None,
  scenario=None, directions=None, no_cache=False)` passes `no_cache` to its query step. With an
  in-memory L1: `synthesize(q)` then `synthesize(q, no_cache=True)` → the second's `query.cached` is
  false; `synthesize(q, no_cache=True)` then `synthesize(q)` → the second's `query.cached` is false.
  Without the flag, the second `synthesize(q)` has `query.cached` true, as today. Exact. Oracle:
  this requirement.
- **C4**: REST. `api.models.QueryRequest` has `no_cache: bool = False`. With the app built by
  `api.app.create_app` over the fixture pipeline (in-memory L1):
  - `POST /query` `{"question": q}` then `{"question": q, "no_cache": true}` → the second response
    has `cached` false; with `mode: "synthesize"` the same holds for the synthesize response;
  - `{"question": q, "no_cache": true}` then `{"question": q}` → the second has `cached` false;
  - `POST /query/stream` with `no_cache: true` after an identical `POST /query` → its `retrieved`
    and `answer` events have `cached` false;
  - `"no_cache": "maybe"` → HTTP 422.

  Exact. Oracle: this requirement and FastAPI/pydantic's documented 422 on a field that fails
  validation.
- **C5**: MCP. In the server built by `mcp_server.server.build_mcp` over the fixture pipeline, the
  tools `macro_query` and `macro_synthesize_shock_vector` accept `no_cache: bool` (default false;
  it appears in each tool's input schema) and pass it to the pipeline: after an identical call,
  a call with `no_cache: true` returns `cached` false. Exact. Oracle: this requirement.

## Interface

Existing, gaining a keyword (default keeps today's behaviour):
- `pipelines.query_pipeline.QueryPipeline.answer(..., no_cache: bool = False) -> QueryResult`.
- `pipelines.query_pipeline.QueryPipeline.synthesize(..., no_cache: bool = False) -> SynthesisResult`.
- `api.models.QueryRequest.no_cache: bool = False`.
- MCP tools `macro_query(..., no_cache: bool = False)` and
  `macro_synthesize_shock_vector(..., no_cache: bool = False)`.

A spec branch may not change existing signatures, so the tests first assert that the keyword
exists (`inspect.signature`, the pydantic model fields, the MCP input schema) and fail by assertion
until it does.

## Notes

- **Not cached today, and still not:** the shock vector. `synthesize` re-runs synthesis, the
  citation gate and validation on every call; only its query step goes through the caches.
- **Why not "don't cache an empty shock vector"** (faultline's original ask): the shock vector is
  never cached, so nothing to change; the persistent empty vectors came from missing episode cards
  ([episode-card-horizons](episode-card-horizons.md)).
- **Cost.** A `no_cache` request costs a full retrieval and generation; cost tracking and the cost
  cap apply to it as to any uncached request.
- Fixtures: the cache-scope in-memory pipeline (hashing embedder, in-memory store, identity
  reranker, scripted `FakeLLM`, disabled tracer), FastAPI `TestClient`, and the MCP server in
  process, as in synth-direction's tests. No network, no `data/`, no clock.
