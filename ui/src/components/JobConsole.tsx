import { useEffect, useRef, useState } from "react";
import { isTerminal, useCancelJob, type JobStatus } from "@/api/admin";
import { authHeaders } from "@/api/client";
import { getSse } from "@/lib/sse";
import { downloadText } from "@/lib/download";

const TONE: Record<JobStatus, string> = {
  queued: "",
  running: "warn",
  succeeded: "good",
  failed: "bad",
  cancelled: "warn",
};

interface LogLine {
  ts: string;
  msg: string;
}

// Live console for a background job: follows /admin/jobs/{id}/stream over SSE for
// real-time logs + terminal status (falling back to nothing if the stream errors),
// with a Cancel button and a log download. Fires onDone once on a terminal state.
// Reused across the maintenance screens (index, eval, ingest, curation).
export function JobConsole({
  jobId,
  onDone,
}: {
  jobId: string;
  onDone?: () => void;
}) {
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [status, setStatus] = useState<JobStatus | undefined>();
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const firedRef = useRef(false);
  const cancel = useCancelJob();

  useEffect(() => {
    const ctrl = new AbortController();
    firedRef.current = false;
    setLogs([]);
    setStatus(undefined);
    setResult(null);
    setError(null);

    getSse({
      path: `/api/admin/jobs/${encodeURIComponent(jobId)}/stream`,
      headers: authHeaders(),
      signal: ctrl.signal,
      onFrame: (f) => {
        if (f.event === "log") {
          try {
            setLogs((prev) => [...prev, JSON.parse(f.data) as LogLine]);
          } catch {
            /* ignore malformed frame */
          }
        } else if (f.event === "status") {
          try {
            const s = JSON.parse(f.data);
            setStatus(s.status);
            setResult(s.result ?? null);
            setError(s.error ?? null);
          } catch {
            /* ignore */
          }
        }
      },
    }).catch((e) => {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    });

    return () => ctrl.abort();
  }, [jobId]);

  useEffect(() => {
    if (isTerminal(status) && !firedRef.current) {
      firedRef.current = true;
      onDone?.();
    }
  }, [status, onDone]);

  function downloadLogs() {
    downloadText(`${jobId}.log`, logs.map((l) => `${l.ts} ${l.msg}`).join("\n"));
  }

  const canCancel = status != null && !isTerminal(status);

  return (
    <div className="card col">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row">
          <code>{jobId}</code>
          {status && <span className={`badge ${TONE[status]}`}>{status}</span>}
        </div>
        <div className="row" style={{ gap: 8 }}>
          {result && (
            <span className="faint" style={{ fontSize: 12 }}>
              {Object.entries(result)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            </span>
          )}
          {canCancel && (
            <button
              className="chip"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate(jobId)}
              title="request cancellation (stops at the next safe checkpoint)"
            >
              {cancel.isPending ? "cancelling…" : "cancel"}
            </button>
          )}
          {logs.length > 0 && (
            <button className="chip" onClick={downloadLogs} title="download job log">
              download log
            </button>
          )}
        </div>
      </div>

      <pre
        style={{
          margin: 0,
          maxHeight: 260,
          overflow: "auto",
          background: "var(--bg)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: 10,
          fontSize: 12,
        }}
      >
        {logs.map((l, i) => (
          <div key={i}>
            <span className="faint">{l.ts.slice(11, 19)} </span>
            {l.msg}
          </div>
        ))}
        {logs.length === 0 && <span className="faint">waiting for output…</span>}
      </pre>

      {error && <div className="callout bad">{error}</div>}
    </div>
  );
}
