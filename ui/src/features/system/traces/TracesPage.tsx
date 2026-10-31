import { useState } from "react";
import { useRouterState } from "@tanstack/react-router";
import {
  useTraces,
  useTrace,
  type TraceRecord,
  type TraceUsage,
  type TraceAnswer,
  type LlmCall,
} from "@/api/admin";
import { Async } from "@/components/Async";
import { Drawer } from "@/components/Drawer";
import { ChunkIdChip } from "@/components/Citations";
import { RouteBadge } from "@/components/RouteBadge";
import { ScoreBar } from "@/components/ScoreBar";
import { Waterfall } from "./Waterfall";
import { ScoreEvolution } from "./ScoreEvolution";
import { traceTotalMs } from "@/lib/traceStats";

// P2 — Trace Explorer: list recent query traces; drill into one for the stage
// waterfall, retrieved evidence and outcome. (GET /admin/traces[/{i}])
export function TracesPage() {
  const traces = useTraces(100);
  const [selected, setSelected] = useState<TraceRecord | null>(null);
  // Seed the query filter from ?q= (a RouteBadge deep-link from Ask/Factors).
  const urlSearch = useRouterState({ select: (s) => s.location.search }) as {
    q?: string;
  };
  const [q, setQ] = useState(urlSearch.q ?? "");
  const [route, setRoute] = useState("ALL");
  const [outcome, setOutcome] = useState<"all" | "answered" | "refused">("all");
  const [minCost, setMinCost] = useState("");

  const all = traces.data?.traces ?? [];
  const routes = Array.from(new Set(all.map((t) => t.route).filter(Boolean))) as string[];
  const minCostNum = Number(minCost);
  const filtered = all.filter((t) => {
    if (q && !(t.query ?? "").toLowerCase().includes(q.toLowerCase())) return false;
    if (route !== "ALL" && t.route !== route) return false;
    if (outcome === "answered" && t.refused) return false;
    if (outcome === "refused" && !t.refused) return false;
    if (minCost.trim() && Number.isFinite(minCostNum) &&
        (t.usage?.cost_usd ?? 0) < minCostNum) return false;
    return true;
  });
  const nActive =
    (q ? 1 : 0) + (route !== "ALL" ? 1 : 0) + (outcome !== "all" ? 1 : 0) +
    (minCost.trim() ? 1 : 0);

  return (
    <div className="content">
      <h1 className="page-title">Traces</h1>
      <p className="page-sub">
        End-to-end request traces — route, stage timings, retrieved evidence and
        outcome. Most recent first.
      </p>

      <Async q={traces}>
        {traces.data && (
          <>
          <div className="card row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12, alignItems: "center" }}>
            <input
              className="input"
              placeholder="search query…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ minWidth: 200, flex: 1 }}
            />
            <select className="input" value={route} onChange={(e) => setRoute(e.target.value)}>
              <option value="ALL">all routes</option>
              {routes.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
            <select
              className="input"
              value={outcome}
              onChange={(e) => setOutcome(e.target.value as typeof outcome)}
            >
              <option value="all">all outcomes</option>
              <option value="answered">answered</option>
              <option value="refused">refused</option>
            </select>
            <label className="row faint" style={{ fontSize: 13, gap: 6 }}>
              min $
              <input
                className="input"
                type="number"
                step="0.001"
                min={0}
                value={minCost}
                onChange={(e) => setMinCost(e.target.value)}
                style={{ width: 90 }}
              />
            </label>
            <span className="faint" style={{ fontSize: 12 }}>
              {filtered.length} / {all.length}
            </span>
            {nActive > 0 && (
              <button
                className="chip"
                onClick={() => {
                  setQ("");
                  setRoute("ALL");
                  setOutcome("all");
                  setMinCost("");
                }}
              >
                clear
              </button>
            )}
          </div>
          <div className="card" style={{ padding: 0 }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                  <Th>When</Th>
                  <Th>Query</Th>
                  <Th>Route</Th>
                  <Th>Chunks</Th>
                  <Th>Outcome</Th>
                  <Th>Cost</Th>
                  <Th>Latency</Th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((t) => (
                  <tr
                    key={t.i}
                    onClick={() => setSelected(t)}
                    style={{ cursor: "pointer", borderTop: "1px solid var(--border)" }}
                  >
                    <Td className="faint">{shortTs(t.ts)}</Td>
                    <Td>
                      <span style={{ display: "block", maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {t.query ?? "—"}
                      </span>
                    </Td>
                    <Td><code>{t.route ?? "—"}</code></Td>
                    <Td>{t.retrieved?.length ?? "—"}</Td>
                    <Td>
                      {t.refused ? (
                        <span className="badge warn">refused</span>
                      ) : (
                        <span className="badge good">answered</span>
                      )}
                    </Td>
                    <Td className="faint">{fmtCost(t.usage?.cost_usd, t.usage?.cost_complete)}</Td>
                    <Td className="faint">{fmtMs(traceTotalMs(t))}</Td>
                  </tr>
                ))}
                {all.length > 0 && filtered.length === 0 && (
                  <tr>
                    <Td className="muted">No traces match the current filters.</Td>
                  </tr>
                )}
                {all.length === 0 && (
                  <tr>
                    <Td className="muted">No traces recorded yet.</Td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          </>
        )}
      </Async>

      {selected && (
        <TraceDetail trace={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function TraceDetail({ trace, onClose }: { trace: TraceRecord; onClose: () => void }) {
  // Lazily fetch the full record — the list omits per-call prompt/response bodies.
  const detailQ = useTrace(trace.i);
  const full = detailQ.data ?? trace;
  const usage = full.usage ?? trace.usage ?? undefined;
  const answer = full.answer;
  // Older traces predate the per-call log / answer capture — tolerate their absence.
  const callLog = usage?.call_log ?? [];

  return (
    <Drawer title="Trace" onClose={onClose}>
      <div className="col">
        <div className="row" style={{ flexWrap: "wrap" }}>
          {trace.route && <RouteBadge route={trace.route} />}
          {trace.kind === "synthesize" && <span className="badge">synthesize</span>}
          {trace.refused ? (
            <span className="badge warn">refused</span>
          ) : (
            <span className="badge good">answered</span>
          )}
          <span className="faint">{shortTs(trace.ts)}</span>
        </div>

        <p style={{ margin: 0 }}>{trace.query ?? "—"}</p>

        {answer && !answer.refused && <FinalAnswer answer={answer} />}

        {usage && <UsageSummary usage={usage} />}

        {callLog.length > 0 && (
          <Section title={`LLM calls (${callLog.length})`}>
            {detailQ.isLoading && (
              <p className="faint">Loading prompts &amp; responses…</p>
            )}
            <div className="col" style={{ gap: 8 }}>
              {callLog.map((c, i) => (
                <CallCard key={i} call={c} />
              ))}
            </div>
          </Section>
        )}

        <Section title="Stage timings">
          <Waterfall timings={trace.timings_ms ?? {}} />
        </Section>

        <Section title="Query analysis">
          <KV k="branches" v={String(trace.n_branches ?? "—")} />
          <KV k="details" v={String(trace.n_details ?? "—")} />
          <KV k="unsupported (dropped)" v={String(trace.n_unsupported ?? "—")} />
          <Facets trace={trace} />
        </Section>

        {trace.timeseries && trace.timeseries.length > 0 && (
          <Section title="Series hydrated">
            <div className="tag-list">
              {trace.timeseries.map((s) => (
                <span key={s} className="badge">{s}</span>
              ))}
            </div>
          </Section>
        )}

        {trace.retrieved && trace.retrieved.length > 0 && (
          <Section title="Score evolution (fused → rerank)">
            <ScoreEvolution retrieved={trace.retrieved} />
          </Section>
        )}

        <Section title={`Retrieved (${trace.retrieved?.length ?? 0})`}>
          <div className="col" style={{ gap: 6 }}>
            {(trace.retrieved ?? []).map((r) => (
              <div
                key={r.chunk_id}
                className="row"
                style={{ justifyContent: "space-between" }}
              >
                <div className="row" style={{ flexWrap: "wrap" }}>
                  <ChunkIdChip chunkId={r.chunk_id} />
                  <span className="faint" style={{ fontSize: 12 }}>{r.source_id}</span>
                  {r.expanded && <span className="badge warn">expanded</span>}
                </div>
                <ScoreBar value={r.rerank_score ?? r.score} />
              </div>
            ))}
            {(trace.retrieved?.length ?? 0) === 0 && (
              <p className="faint">No chunks recorded.</p>
            )}
          </div>
        </Section>
      </div>
    </Drawer>
  );
}

// ---- final answer ----

function FinalAnswer({ answer }: { answer: TraceAnswer }) {
  return (
    <Section title="Final answer">
      {answer.summary ? (
        <p style={{ margin: 0 }}>{answer.summary}</p>
      ) : (
        <p className="faint">No summary generated.</p>
      )}
      {answer.details.length > 0 && (
        <div className="col" style={{ gap: 8, marginTop: 4 }}>
          {answer.details.map((d, i) => (
            <div key={i}>
              <span>{d.text} </span>
              <span className="tag-list" style={{ display: "inline-flex" }}>
                {d.chunk_ids.map((c) => (
                  <ChunkIdChip key={c} chunkId={c} />
                ))}
              </span>
            </div>
          ))}
        </div>
      )}
      {answer.unanswered.length > 0 && (
        <div className="callout warn" style={{ marginTop: 4 }}>
          <strong>Not covered by corpus:</strong>
          <ul style={{ margin: "6px 0 0" }}>
            {answer.unanswered.map((u, i) => <li key={i}>{u}</li>)}
          </ul>
        </div>
      )}
      {answer.unsupported.length > 0 && (
        <div className="callout bad" style={{ marginTop: 4 }}>
          <strong>Dropped by verifier:</strong>
          <ul style={{ margin: "6px 0 0" }}>
            {answer.unsupported.map((u, i) => <li key={i}>{u}</li>)}
          </ul>
        </div>
      )}
    </Section>
  );
}

// ---- usage summary + per-call cards ----

function UsageSummary({ usage }: { usage: Partial<TraceUsage> }) {
  return (
    <Section title="LLM cost & tokens">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))",
          gap: 8,
        }}
      >
        {usage.calls != null && <Stat label="calls" value={fmtInt(usage.calls)} />}
        <Stat label="cost" value={fmtCost(usage.cost_usd, usage.cost_complete)} />
        <Stat label="total tokens" value={fmtInt(usage.total_tokens)} />
        <Stat label="prompt" value={fmtInt(usage.prompt_tokens)} />
        <Stat label="completion" value={fmtInt(usage.completion_tokens)} />
        <Stat label="cached" value={fmtInt(usage.cached_tokens)} />
      </div>
    </Section>
  );
}

function CallCard({ call }: { call: LlmCall }) {
  const [open, setOpen] = useState(false);
  const hasBody = !!(call.system || call.user || call.response);
  return (
    <div className="card" style={{ padding: 10 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{
          all: "unset",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          width: "100%",
        }}
      >
        <span className="faint" style={{ width: 14 }}>{open ? "▾" : "▸"}</span>
        <span className="badge">{call.purpose}</span>
        <code style={{ fontSize: 12 }}>{call.model}</code>
        <span className="row" style={{ gap: 10, marginLeft: "auto", flexWrap: "wrap" }}>
          <span className="faint" style={{ fontSize: 12 }}>
            {fmtInt(call.prompt_tokens)}→{fmtInt(call.completion_tokens)} tok
          </span>
          {call.cached_tokens > 0 && (
            <span className="badge" title="cached prompt tokens">
              {fmtInt(call.cached_tokens)} cached
            </span>
          )}
          <span style={{ fontSize: 12 }}>{fmtCost(call.cost_usd, true)}</span>
          <span className="faint" style={{ fontSize: 12 }}>
            {fmtMs(call.latency_s * 1000)}
          </span>
        </span>
      </button>

      {open && (
        <div className="col" style={{ gap: 8, marginTop: 10 }}>
          {!hasBody && (
            <p className="faint" style={{ fontSize: 12 }}>
              Prompt/response capture is off for this trace (only token/cost were
              recorded).
            </p>
          )}
          {call.system && <PromptBlock label="System" text={call.system} />}
          {call.user && <PromptBlock label="User prompt" text={call.user} />}
          {call.response && <PromptBlock label="Response" text={call.response} />}
        </div>
      )}
    </div>
  );
}

function PromptBlock({ label, text }: { label: string; text: string }) {
  return (
    <div className="col" style={{ gap: 4 }}>
      <span className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>
        {label}
      </span>
      <pre
        style={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontSize: 12,
          margin: 0,
          maxHeight: 320,
          overflow: "auto",
          background: "var(--bg-elev-2)",
          padding: 10,
          borderRadius: 6,
        }}
      >
        {text}
      </pre>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="col" style={{ gap: 2 }}>
      <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 15 }}>{value}</span>
      <span className="faint" style={{ fontSize: 11 }}>{label}</span>
    </div>
  );
}

function Facets({ trace }: { trace: TraceRecord }) {
  const f = trace.filters;
  if (!f) return null;
  const tags = [...(f.factor_tags ?? []), ...(f.entity_tags ?? [])];
  if (tags.length === 0 && !f.date_from && !f.date_to) {
    return <KV k="filters" v="none" />;
  }
  return (
    <div className="col" style={{ gap: 4 }}>
      {tags.length > 0 && (
        <div className="tag-list">
          {tags.map((t) => (
            <span key={t} className="chip" style={{ cursor: "default" }}>{t}</span>
          ))}
        </div>
      )}
      {(f.date_from || f.date_to) && (
        <KV k="dates" v={`${f.date_from ?? "…"} → ${f.date_to ?? "…"}`} />
      )}
    </div>
  );
}

// ---- tiny presentational helpers ----

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="col" style={{ gap: 8, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <span className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>{title}</span>
      {children}
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="row" style={{ gap: 8 }}>
      <span className="faint" style={{ width: 160 }}>{k}</span>
      <span style={{ fontFamily: "ui-monospace, monospace", fontSize: 13 }}>{v}</span>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th style={{ padding: "10px 14px", fontWeight: 600, fontSize: 12 }}>{children}</th>;
}
function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={className} style={{ padding: "10px 14px", fontSize: 13 }}>{children}</td>;
}

function shortTs(ts?: string): string {
  if (!ts) return "—";
  return ts.replace("T", " ").slice(0, 19);
}
function fmtMs(ms: number): string {
  return ms > 0 ? `${ms.toFixed(0)} ms` : "—";
}
function fmtInt(n?: number): string {
  return n == null ? "—" : n.toLocaleString("en-US");
}
// Cost is an estimate (configs/llm_pricing.yaml). `complete=false` means some
// call used an unpriced model, so the figure is a lower bound → suffix "+".
function fmtCost(usd?: number | null, complete?: boolean): string {
  if (usd == null) return "—";
  const digits = usd > 0 && usd < 0.01 ? 5 : 4;
  return `$${usd.toFixed(digits)}${complete === false ? "+" : ""}`;
}
