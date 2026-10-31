import { useState } from "react";
import {
  useJobs,
  useTier,
  useTiers,
  useTriggerIngest,
  type EndpointConfig,
  type JobSummary,
  type TierSummary,
} from "@/api/admin";
import { Async } from "@/components/Async";
import { JobConsole } from "@/components/JobConsole";

// P4 — Ingestion: tier catalog, endpoint selection, and a tracked ingest run.
export function IngestionPage() {
  const tiers = useTiers();
  const [tier, setTier] = useState<string | null>(null);

  return (
    <div className="content">
      <h1 className="page-title">Ingestion</h1>
      <p className="page-sub">
        Source tiers and endpoints, and on-demand ingestion runs. Mirrors{" "}
        <code>python -m ingest</code>.
      </p>

      <Async q={tiers}>
        {tiers.data && (
          <div className="row" style={{ flexWrap: "wrap" }}>
            {tiers.data.tiers.map((t) => (
              <TierCard
                key={t.name}
                t={t}
                active={t.name === tier}
                onClick={() => setTier(t.name)}
              />
            ))}
          </div>
        )}
      </Async>

      {tier && <TierPanel name={tier} />}

      <RecentRuns />
    </div>
  );
}

function TierCard({ t, active, onClick }: { t: TierSummary; active: boolean; onClick: () => void }) {
  return (
    <button
      className="card"
      onClick={onClick}
      style={{
        flex: "1 1 200px",
        minWidth: 190,
        textAlign: "left",
        cursor: "pointer",
        borderColor: active ? "var(--accent)" : undefined,
      }}
    >
      <div style={{ fontWeight: 700 }}>{t.title ?? t.name}</div>
      <div className="faint" style={{ fontSize: 12 }}>{t.name}</div>
      {t.error ? (
        <span className="badge bad" style={{ marginTop: 6 }}>config error</span>
      ) : (
        <div className="row" style={{ marginTop: 6 }}>
          <span className="badge">tier {t.tier}</span>
          <span className="badge good">{t.n_enabled}/{t.n_endpoints} enabled</span>
        </div>
      )}
    </button>
  );
}

function TierPanel({ name }: { name: string }) {
  const tier = useTier(name);
  const trigger = useTriggerIngest();
  const [selected, setSelected] = useState<string[]>([]);
  const [limit, setLimit] = useState<string>("");
  const [refresh, setRefresh] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);

  function toggle(id: string) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }

  function run() {
    trigger.mutate(
      {
        tier: name,
        endpoints: selected.length ? selected : undefined,
        limit: limit ? Number(limit) : undefined,
        refresh,
      },
      { onSuccess: (j) => setJobId(j.id) },
    );
  }

  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <Async q={tier}>
        {tier.data && (
          <>
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
              <div>
                <strong>{tier.data.title}</strong>{" "}
                <span className="faint">· {tier.data.publisher}</span>
              </div>
              <a href={tier.data.base_url} target="_blank" rel="noreferrer">base ↗</a>
            </div>

            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                  <Th></Th>
                  <Th>Endpoint</Th>
                  <Th>Bucket</Th>
                  <Th>Cadence</Th>
                  <Th>Discovery</Th>
                  <Th>Status</Th>
                </tr>
              </thead>
              <tbody>
                {tier.data.endpoints.map((e: EndpointConfig) => (
                  <tr key={e.id} style={{ borderTop: "1px solid var(--border)" }}>
                    <Td>
                      <input
                        type="checkbox"
                        checked={selected.includes(e.id)}
                        onChange={() => toggle(e.id)}
                      />
                    </Td>
                    <Td>
                      <code>{e.id}</code>
                      <div className="faint" style={{ fontSize: 11 }}>{e.title}</div>
                    </Td>
                    <Td>{e.bucket}</Td>
                    <Td className="faint">{e.poll_cadence}</Td>
                    <Td className="faint">{e.discovery.type}</Td>
                    <Td>
                      {e.enabled ? (
                        <span className="badge good">enabled</span>
                      ) : (
                        <span className="badge">disabled</span>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="row" style={{ flexWrap: "wrap", borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              <span className="faint">
                {selected.length ? `${selected.length} endpoint(s)` : "all enabled endpoints"}
              </span>
              <label className="row" style={{ gap: 6 }}>
                <span className="faint">limit</span>
                <input className="input" style={{ width: 80 }} type="number" min={1}
                       placeholder="all" value={limit}
                       onChange={(e) => setLimit(e.target.value)} />
              </label>
              <label className="row" style={{ gap: 6 }}>
                <input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} />
                <span className="faint">refresh discovery cache</span>
              </label>
              <button className="btn primary" disabled={trigger.isPending} onClick={run}>
                {trigger.isPending ? "Starting…" : "Run ingest"}
              </button>
            </div>
            {trigger.isError && (
              <div className="callout bad">{(trigger.error as Error).message}</div>
            )}
          </>
        )}
      </Async>

      {jobId && <JobConsole jobId={jobId} />}
    </div>
  );
}

function RecentRuns() {
  const jobs = useJobs();
  const ingestJobs = (jobs.data?.jobs ?? []).filter((j) => j.type === "ingest");
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Recent runs</strong>
      <Async q={jobs}>
        {jobs.data &&
          (ingestJobs.length === 0 ? (
            <p className="muted">No ingestion runs yet.</p>
          ) : (
            <>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                    <Th>Job</Th>
                    <Th>Tier</Th>
                    <Th>Status</Th>
                    <Th>Result</Th>
                  </tr>
                </thead>
                <tbody>
                  {ingestJobs.map((j: JobSummary) => (
                    <tr key={j.id} onClick={() => setOpen(j.id)}
                        style={{ cursor: "pointer", borderTop: "1px solid var(--border)" }}>
                      <Td><code>{j.id}</code></Td>
                      <Td className="faint">{String(j.params.tier ?? "—")}</Td>
                      <Td>
                        <span className={`badge ${j.status === "succeeded" ? "good" : j.status === "failed" ? "bad" : "warn"}`}>
                          {j.status}
                        </span>
                      </Td>
                      <Td className="faint">
                        {j.result ? String(j.result.summary ?? "") : j.error ?? "—"}
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {open && <JobConsole jobId={open} />}
            </>
          ))}
      </Async>
    </div>
  );
}

function Th({ children }: { children?: React.ReactNode }) {
  return <th style={{ padding: "10px 14px", fontWeight: 600, fontSize: 12 }}>{children}</th>;
}
function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={className} style={{ padding: "10px 14px", fontSize: 13 }}>{children}</td>;
}
