# admin-index-incremental — run the incremental index refresh as an admin job

## Requirement

The CLI has two index paths: a full rebuild (`index`, `index --append`) and a diff-based refresh
(`index --incremental [--dry-run]`, `pipelines.index_pipeline.run_incremental`) that embeds only
new and changed chunks and deletes departed ones. The admin API (`POST /admin/index`, used by the
UI's maintenance page) offers only the full rebuild: `{"append": bool}`. So an operator without a
shell on the host, or the UI, cannot run the cheap refresh or preview its plan.

The chore before this spec added two inert request fields, `incremental` and `dry_run`, and an
inert `should_cancel` parameter on `run_incremental`. When this change is done:

- **Request.** `POST /admin/index` takes `append`, `incremental` and `dry_run` (all default
  false). `dry_run` without `incremental` is rejected with **422**, and so is `incremental`
  together with `append` (a refresh never recreates or appends wholesale). A rejected request
  submits no job.
- **Job.** An accepted request submits the `index` job, exclusive in the corpus group as today
  (409 while another corpus job runs), with job params `{"append", "incremental", "dry_run"}`
  as given.
- **Runner.** The default index runner runs `run_incremental(settings, dry_run=...,
  should_cancel=job.check_cancel)` when `incremental` is true, and the full build as today
  otherwise. The job result is `run_incremental`'s stats; a dry run writes nothing (no store
  change, no manifest).
- **Cancellation.** `run_incremental` calls `should_cancel` (when given) before it loads the
  embedder or writes anything (opening the store first is allowed: it writes nothing), and
  between embedding batches. When `should_cancel` raises, the
  exception propagates and nothing further is written; called before any write, that means the
  store and manifest are untouched.

## Acceptance criteria

- **C1**: Through the FastAPI app (`api.app.create_app`) with an injected `index_runner` that
  records each job's params: `{}` submits a job with params `{"append": false, "incremental":
  false, "dry_run": false}`; `{"incremental": true}` and `{"incremental": true, "dry_run":
  true}` submit jobs with those params; `{"dry_run": true}` and `{"incremental": true,
  "append": true}` return 422 and submit nothing; a second accepted request while the first job
  still runs returns 409. Oracle: Requirement.
- **C2**: Through the app with the **default** index runner, settings whose processed corpus is a
  temporary directory of synthetic documents and whose Qdrant store is a local temporary path,
  `{"incremental": true, "dry_run": true}` produces a job whose result reports `dry_run: true`,
  `scanned` equal to the number of chunks in the corpus, `new` equal to that number on an empty
  store, and `indexed`, `changed`, `unchanged`, `deleted` and `stale` 0; afterwards the store holds no points and no manifest exists. Oracle:
  Requirement and `index_chunks_incremental`'s stats contract.
- **C3**: `pipelines.index_pipeline.run_incremental(settings, dry_run=d, should_cancel=f)`, for
  `d` true or false, with an `f` that raises on its first call, propagates that exception, and
  the store and manifest are unchanged; with an `f` that never raises and `d` true, it returns
  the same stats as without `f`. Oracle: Requirement.

## Interface

No new functions. Exercised as is: `api.app.create_app`, `api.admin.IndexRequest`,
`pipelines.index_pipeline.run_incremental`, `index_chunks_incremental`, `utils.config.RagSettings`,
`api.jobs.JobManager`.

## Notes

- No torch and no network: a dry run loads no embedder, C3's raising `should_cancel` stops before
  the embedder is built, and Qdrant runs in local (file) mode on a temporary path. The tests set
  `RAGINFER_URL` to an unroutable non-HTTP URL with local fallback off, so an embedder that is
  built is a `RemoteEmbedder` that fails at once if called, never bge-m3 and never a socket;
  C3's `dry_run=false` case pre-indexes the corpus with the hashing embedder so nothing is left
  to embed. Verified on 2026-10-21: `RagSettings(qdrant={"url": None, "path":
  <tmp>/qdrant})` with `paths.processed_dir` and `paths.manifest` pointed at a temporary
  directory, over two synthetic one-chunk documents, gives `run_incremental(s, dry_run=True)` ==
  `{"scanned": 2, "new": 2, "changed": 0, "unchanged": 0, "indexed": 0, "deleted": 0,
  "dry_run": True, "stale": 0}` and no manifest file. Test sketch: `test/test_jobs.py` (app with an injected runner and a
  temporary manifest path) and `test/test_incremental_index.py` (synthetic processed documents).
- Out of scope: the UI toggle on the maintenance page (implementation, checked by the UI build),
  and incremental mode for the MCP surface, which has no index tool.
