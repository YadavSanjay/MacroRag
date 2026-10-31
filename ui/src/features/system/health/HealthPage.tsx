import {
  useEvalBaseline,
  useHealthChecks,
  useJobs,
  useManifest,
  useManifestHistory,
  useMetrics,
  useTraces,
  type AdminManifestDto,
  type HealthCheck,
  type MetricsDto,
} from "@/api/admin";
import { useHealth } from "@/api/hooks";
import { Async, StatCard } from "@/components/Async";
import { summarizeTraces } from "@/lib/traceStats";

const HEALTH_TONE: Record<string, string> = {
  ok: "badge good",
  degraded: "badge warn",
  down: "badge bad",
  skipped: "badge",
};

function usd(n: number): string {
  return n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(4)}`;
}

// P2 — system health: liveness + manifest/staleness + recent-activity summary
// derived from query traces.
export function HealthPage() {
  const health = useHealth();
  const manifest = useManifest();
  const traces = useTraces(200);

  return (
    <div className="content">
      <h1 className="page-title">Health</h1>
      <p className="page-sub">
        Liveness, index manifest &amp; staleness, and recent query activity.
      </p>

      <AlertsPanel manifest={manifest.data} />

      <DependencyPanel />

      <Async q={manifest}>
        {manifest.data && (
          <>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <StatCard
                label="Service"
                value={health.isError ? "offline" : health.data?.built ? "ready" : "not built"}
                tone={health.isError ? "bad" : health.data?.built ? "good" : "warn"}
              />
              <StatCard
                label="Index"
                value={manifest.data.stale ? "stale" : "fresh"}
                tone={manifest.data.stale ? "warn" : "good"}
                sub={manifest.data.stale ? "rebuild required" : "embedding signature matches"}
              />
              <StatCard
                label="World view"
                value={manifest.data.world_view_id ?? "—"}
                sub={
                  manifest.data.manifest?.corpus_digest
                    ? "content-addressed corpus state"
                    : "corpus state (legacy date-only id)"
                }
              />
              <StatCard
                label="Chunks"
                value={(manifest.data.manifest?.chunks ?? 0).toLocaleString()}
              />
              <StatCard
                label="Observations"
                value={(manifest.data.manifest?.observations ?? 0).toLocaleString()}
              />
            </div>

            {manifest.data.stale && (
              <div className="callout warn" style={{ marginTop: 12 }}>
                The embedding signature changed since the last build — the vector index
                must be rebuilt before answers reflect the current pipeline.
              </div>
            )}

            <ManifestPanel m={manifest.data} />
            <BuildHistoryPanel />
          </>
        )}
      </Async>

      <CostPanel />

      <div className="card col" style={{ marginTop: 16 }}>
        <strong>Recent activity</strong>
        <Async q={traces}>
          {traces.data && <Activity traces={traces.data.traces} />}
        </Async>
      </div>
    </div>
  );
}

// Corpus-state provenance: every recorded index build, newest first, with what
// changed against the build before it. This is an audit *trail* — it proves what
// a corpus was; it does not let you retrieve against a superseded one.
function BuildHistoryPanel() {
  const q = useManifestHistory();
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Build history</strong>
        {q.data?.current && <span className="faint">live: {q.data.current}</span>}
      </div>
      <span className="faint">
        Each build is content-addressed, so an unchanged corpus keeps its id and two
        same-day corpora no longer collide.
      </span>
      <Async q={q}>
        {q.data &&
          (q.data.builds.length === 0 ? (
            <p className="muted">
              No builds recorded yet — the next index run starts the history.
            </p>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                    <HTh>Built</HTh>
                    <HTh>World view</HTh>
                    <HTh>Chunks</HTh>
                    <HTh>Changed vs previous</HTh>
                  </tr>
                </thead>
                <tbody>
                  {q.data.builds.map((b, i) => (
                    <tr
                      key={b.world_view_id ?? i}
                      style={{
                        borderTop: "1px solid var(--border)",
                        background:
                          b.world_view_id === q.data.current
                            ? "var(--bg-elev-2)"
                            : undefined,
                      }}
                    >
                      <HTd className="faint">
                        {(b.built_at ?? "").replace("T", " ").slice(0, 19)}
                      </HTd>
                      <HTd>
                        {b.world_view_id ?? "—"}
                        {b.world_view_id === q.data.current && (
                          <span className="badge good" style={{ marginLeft: 6 }}>
                            live
                          </span>
                        )}
                        {!b.corpus_digest && (
                          <span className="badge" style={{ marginLeft: 6 }}>
                            legacy
                          </span>
                        )}
                      </HTd>
                      <HTd>{(b.chunks ?? 0).toLocaleString()}</HTd>
                      <HTd className="faint" style={{ fontSize: 12 }}>
                        {b.changes && b.changes.length > 0
                          ? b.changes.map((c) => c.field).join(", ")
                          : "—"}
                      </HTd>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </Async>
    </div>
  );
}

function HTh({ children }: { children: React.ReactNode }) {
  return (
    <th style={{ padding: "10px 14px", fontWeight: 600, fontSize: 12 }}>{children}</th>
  );
}
function HTd({
  children,
  className,
  style,
}: {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <td className={className} style={{ padding: "10px 14px", fontSize: 13, ...style }}>
      {children}
    </td>
  );
}

// Live dependency probes: vector store, time-series store, inference service, LLM
// provider credential. Overall status badge + per-dependency detail & latency.
function DependencyPanel() {
  const health = useHealthChecks();
  return (
    <div className="card col" style={{ marginBottom: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Dependencies</strong>
        {health.data && (
          <span className={HEALTH_TONE[health.data.status] ?? "badge"}>
            ● {health.data.status}
          </span>
        )}
      </div>
      <Async q={health} loadingText="Probing dependencies…">
        {health.data && (
          <div className="col" style={{ gap: 6 }}>
            {health.data.checks.map((c) => (
              <DependencyRow key={c.name} check={c} />
            ))}
          </div>
        )}
      </Async>
    </div>
  );
}

function DependencyRow({ check }: { check: HealthCheck }) {
  return (
    <div
      className="row"
      style={{ justifyContent: "space-between", gap: 12, alignItems: "baseline" }}
    >
      <div className="row" style={{ gap: 8, minWidth: 0 }}>
        <span className={HEALTH_TONE[check.status] ?? "badge"}>{check.status}</span>
        <code>{check.name}</code>
        <span className="faint" style={{ fontSize: 12 }}>
          {check.detail}
        </span>
      </div>
      <span className="faint" style={{ fontSize: 11, whiteSpace: "nowrap" }}>
        {check.latency_ms} ms
      </span>
    </div>
  );
}

// Cost, token, and latency roll-up over the query traces (Tier 2). Complements
// per-trace cost in the Trace Explorer with spend-over-time + by route/model.
function CostPanel() {
  const metrics = useMetrics();
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Cost &amp; usage</strong>
      <Async q={metrics} loadingText="Aggregating traces…">
        {metrics.data && <CostBody m={metrics.data} />}
      </Async>
    </div>
  );
}

function CostBody({ m }: { m: MetricsDto }) {
  if (m.n_traces === 0) {
    return <p className="muted">No query traces recorded yet.</p>;
  }
  const maxDaily = Math.max(...m.daily.map((d) => d.cost_usd), 1e-9);
  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <StatCard
          label="Total cost"
          value={usd(m.cost.total_usd)}
          sub={m.cost.complete ? `${m.n_traces} traces` : "lower bound (partial pricing)"}
          tone={m.cost.complete ? undefined : "warn"}
        />
        <StatCard
          label="Total tokens"
          value={m.cost.total_tokens.toLocaleString()}
          sub={`${m.cost.cached_tokens.toLocaleString()} cached`}
        />
        <StatCard label="Avg latency" value={`${m.latency_ms.avg.toFixed(0)} ms`} />
        <StatCard label="p95 latency" value={`${m.latency_ms.p95.toFixed(0)} ms`} />
        <StatCard
          label="Cache hit rate"
          value={`${(m.queries.cache_hit_rate * 100).toFixed(0)}%`}
        />
      </div>

      {m.daily.length > 1 && (
        <div className="col" style={{ gap: 6 }}>
          <span className="faint" style={{ fontSize: 11 }}>
            DAILY SPEND
          </span>
          {m.daily.map((d) => (
            <div key={d.date} className="row" style={{ gap: 8 }}>
              <code style={{ width: 90 }}>{d.date}</code>
              <span className="scorebar-track" style={{ width: 200 }}>
                <span
                  className="scorebar-fill"
                  style={{ width: `${(d.cost_usd / maxDaily) * 100}%` }}
                />
              </span>
              <span className="faint" style={{ fontSize: 12 }}>
                {usd(d.cost_usd)} · {d.n} q · {d.avg_latency_ms.toFixed(0)} ms
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="row" style={{ gap: 24, flexWrap: "wrap", alignItems: "flex-start" }}>
        <div className="col" style={{ gap: 4, minWidth: 260 }}>
          <span className="faint" style={{ fontSize: 11 }}>
            BY ROUTE
          </span>
          {m.by_route.map((r) => (
            <div key={r.route} className="row" style={{ justifyContent: "space-between", gap: 12 }}>
              <code>{r.route}</code>
              <span className="faint" style={{ fontSize: 12 }}>
                {r.n} q · {usd(r.cost_usd)} · {r.avg_latency_ms.toFixed(0)} ms
                {r.refused ? ` · ${r.refused} refused` : ""}
              </span>
            </div>
          ))}
        </div>
        <div className="col" style={{ gap: 4, minWidth: 260 }}>
          <span className="faint" style={{ fontSize: 11 }}>
            BY MODEL
          </span>
          {m.by_model.map((mm) => (
            <div key={mm.model} className="row" style={{ justifyContent: "space-between", gap: 12 }}>
              <code>{mm.model}</code>
              <span className="faint" style={{ fontSize: 12 }}>
                {mm.calls} calls · {mm.total_tokens.toLocaleString()} tok · {usd(mm.cost_usd)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

interface Alert {
  tone: "warn" | "bad" | "good";
  text: string;
}

// Composed operational alerts from manifest staleness, recent job failures, and
// the drift baseline. Derived client-side from the admin reads.
function AlertsPanel({ manifest }: { manifest?: AdminManifestDto }) {
  const jobs = useJobs();
  const baseline = useEvalBaseline();

  const alerts: Alert[] = [];
  if (manifest) {
    if (!manifest.built) alerts.push({ tone: "warn", text: "Pipeline not built — first query will load models." });
    if (manifest.stale) alerts.push({ tone: "warn", text: "Index is stale — a rebuild is required." });
  }
  const failed = (jobs.data?.jobs ?? []).filter((j) => j.status === "failed");
  if (failed.length) {
    alerts.push({
      tone: "bad",
      text: `${failed.length} job(s) failed recently: ${failed.slice(0, 3).map((j) => j.id).join(", ")}`,
    });
  }
  if (baseline.data && baseline.data.baseline == null) {
    alerts.push({ tone: "warn", text: "No drift baseline set — run an eval and promote it." });
  }

  return (
    <div className="card col" style={{ marginBottom: 16 }}>
      <strong>Alerts</strong>
      {alerts.length === 0 ? (
        <div className="callout">
          <span className="badge good">● all clear</span> no active alerts.
        </div>
      ) : (
        <div className="col" style={{ gap: 6 }}>
          {alerts.map((a, i) => (
            <div key={i} className={`callout ${a.tone === "good" ? "" : a.tone}`}>
              {a.text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ManifestPanel({ m }: { m: NonNullable<ReturnType<typeof useManifest>["data"]> }) {
  const man = m.manifest;
  const rows: [string, string][] = [
    ["Pipeline version", m.pipeline_version],
    ["Built at", man?.built_at ?? "—"],
    ["Embedding", man ? `${man.embedding.model} · ${man.embedding.dense_dim}d` : "—"],
    ["Grounding prefix", man ? String(man.embedding.grounding_prefix) : "—"],
    ["Reranker", man?.reranker ?? "—"],
    ["LLM (answer / complex)", man ? `${man.llm.answer} / ${man.llm.complex}` : "—"],
    ["Collection", man?.qdrant_collection ?? "—"],
    ["Prompt hash", m.prompt_hash],
  ];
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Manifest</strong>
      <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", rowGap: 6 }}>
        {rows.map(([k, v]) => (
          <FragmentRow key={k} k={k} v={v} />
        ))}
      </div>
    </div>
  );
}

function FragmentRow({ k, v }: { k: string; v: string }) {
  return (
    <>
      <div className="faint">{k}</div>
      <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 13 }}>{v}</div>
    </>
  );
}

function Activity({ traces }: { traces: Parameters<typeof summarizeTraces>[0] }) {
  const s = summarizeTraces(traces);
  if (s.n === 0) {
    return <p className="muted">No query traces recorded yet.</p>;
  }
  const max = Math.max(...s.routeMix.map((r) => r.count), 1);
  return (
    <div className="col">
      <div className="row" style={{ flexWrap: "wrap" }}>
        <StatCard label="Traced queries" value={s.n} />
        <StatCard
          label="Refusal rate"
          value={`${(s.refusalRate * 100).toFixed(0)}%`}
          tone={s.refusalRate > 0.25 ? "warn" : undefined}
        />
        <StatCard label="Avg latency" value={`${s.avgMs.toFixed(0)} ms`} />
        <StatCard label="p95 latency" value={`${s.p95Ms.toFixed(0)} ms`} />
      </div>
      <div className="col" style={{ gap: 6 }}>
        <span className="faint" style={{ fontSize: 11 }}>
          ROUTE MIX
        </span>
        {s.routeMix.map((r) => (
          <div key={r.route} className="row" style={{ gap: 8 }}>
            <code style={{ width: 130 }}>{r.route}</code>
            <span className="scorebar-track" style={{ width: 200 }}>
              <span
                className="scorebar-fill"
                style={{ width: `${(r.count / max) * 100}%` }}
              />
            </span>
            <span className="faint">{r.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
