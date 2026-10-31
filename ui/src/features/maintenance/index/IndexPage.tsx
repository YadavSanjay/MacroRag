import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  isTerminal,
  useJobs,
  useManifest,
  useTriggerIndex,
  type IndexMode,
  type JobSummary,
} from "@/api/admin";
import { Async, StatCard } from "@/components/Async";
import { JobConsole } from "@/components/JobConsole";
import { Table, Td, Th } from "@/components/Table";

// P4 — Index management: manifest + staleness and index build/append as a tracked job.
export function IndexPage() {
  const manifest = useManifest();
  const jobs = useJobs();
  const trigger = useTriggerIndex();
  const qc = useQueryClient();
  const [activeJob, setActiveJob] = useState<string | null>(null);

  const indexJobs = (jobs.data?.jobs ?? []).filter((j) => j.type === "index");
  const running = indexJobs.some((j) => !isTerminal(j.status)) || trigger.isPending;

  function build(mode: IndexMode) {
    trigger.mutate(mode, { onSuccess: (job) => setActiveJob(job.id) });
  }

  function onJobDone() {
    qc.invalidateQueries({ queryKey: ["admin", "manifest"] });
    qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
  }

  return (
    <div className="content">
      <h1 className="page-title">Index</h1>
      <p className="page-sub">
        Build status and rebuilds. A build embeds every chunk into the vector store,
        loads numeric rows into the time-series store, and writes the manifest.
      </p>

      <Async q={manifest}>
        {manifest.data && (
          <>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <StatCard
                label="Index"
                value={manifest.data.stale ? "stale" : "fresh"}
                tone={manifest.data.stale ? "warn" : "good"}
              />
              <StatCard label="Built at" value={shortTs(manifest.data.manifest?.built_at)} />
              <StatCard label="Chunks" value={(manifest.data.manifest?.chunks ?? 0).toLocaleString()} />
              <StatCard label="Observations" value={(manifest.data.manifest?.observations ?? 0).toLocaleString()} />
              <StatCard label="World view" value={manifest.data.world_view_id ?? "—"} />
            </div>

            {manifest.data.stale && (
              <div className="callout warn" style={{ marginTop: 12 }}>
                The embedding signature changed since the last build — a full rebuild is
                required for answers to reflect the current pipeline.
              </div>
            )}
          </>
        )}
      </Async>

      <div className="card col" style={{ marginTop: 16 }}>
        <strong>Build</strong>
        <p className="muted" style={{ margin: 0 }}>
          Refresh embeds only new and changed chunks and removes departed ones — the quick
          path after an incremental ingest; preview shows its plan without writing anything.
          Rebuild re-embeds everything (needed after an embedder or chunking change); append
          adds every chunk to the existing collection.
        </p>
        <div className="row">
          <button className="btn primary" disabled={running}
                  onClick={() => build({ incremental: true })}>
            {running ? "Build running…" : "Refresh changed"}
          </button>
          <button className="btn" disabled={running}
                  onClick={() => build({ incremental: true, dry_run: true })}>
            Preview refresh
          </button>
          <button className="btn" disabled={running} onClick={() => build({})}>
            Rebuild index
          </button>
          <button className="btn" disabled={running} onClick={() => build({ append: true })}>
            Append
          </button>
        </div>
        {trigger.isError && (
          <div className="callout bad">{(trigger.error as Error).message}</div>
        )}
      </div>

      {activeJob && (
        <div className="col" style={{ marginTop: 16 }}>
          <strong>Current build</strong>
          <JobConsole jobId={activeJob} onDone={onJobDone} />
        </div>
      )}

      <div className="card col" style={{ marginTop: 16 }}>
        <strong>Recent builds</strong>
        <Async q={jobs}>
          {jobs.data && <JobsTable jobs={indexJobs} onOpen={setActiveJob} />}
        </Async>
      </div>
    </div>
  );
}

function JobsTable({ jobs, onOpen }: { jobs: JobSummary[]; onOpen: (id: string) => void }) {
  if (jobs.length === 0) return <p className="muted">No index builds yet.</p>;
  return (
    <Table>
      <thead>
        <tr>
          <Th>Job</Th>
          <Th>Status</Th>
          <Th>Mode</Th>
          <Th>Result</Th>
          <Th>Finished</Th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((j) => (
          <tr key={j.id} className="clickable" onClick={() => onOpen(j.id)}>
            <Td><code>{j.id}</code></Td>
            <Td>
              <span className={`badge ${j.status === "succeeded" ? "good" : j.status === "failed" ? "bad" : "warn"}`}>
                {j.status}
              </span>
            </Td>
            <Td className="faint">{indexModeLabel(j.params)}</Td>
            <Td className="faint">
              {j.result
                ? Object.entries(j.result).map(([k, v]) => `${k}: ${v}`).join(" · ")
                : j.error ?? "—"}
            </Td>
            <Td className="faint">{shortTs(j.finished_at)}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function indexModeLabel(params: JobSummary["params"]): string {
  if (params.incremental) return params.dry_run ? "refresh (preview)" : "refresh";
  return params.append ? "append" : "recreate";
}

function shortTs(ts?: string | null): string {
  return ts ? ts.replace("T", " ").slice(0, 19) : "—";
}
