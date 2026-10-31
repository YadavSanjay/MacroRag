import { useState } from "react";
import {
  useTimeseriesQuery,
  useTimeseriesStore,
  useVectorProbe,
  useVectorStore,
  type TimeseriesStoreDto,
  type TsQueryParams,
} from "@/api/admin";
import { Async, StatCard } from "@/components/Async";
import { ChunkIdChip } from "@/components/Citations";
import { ScoreBar } from "@/components/ScoreBar";
import { downloadCsv } from "@/lib/download";

// P2 + Tier 3 — vector + time-series store inspectors, now with drill-down: a raw
// vector probe (pre-rerank candidates) and a time-series query builder.
export function StoresPage() {
  const vec = useVectorStore();
  const ts = useTimeseriesStore();

  return (
    <div className="content">
      <h1 className="page-title">Stores</h1>
      <p className="page-sub">
        The hybrid vector store (prose chunks) and the SQLite time-series store
        (exact numbers) behind retrieval.
      </p>

      <div className="card col">
        <strong>Vector store</strong>
        <Async q={vec}>
          {vec.data && (
            <div className="row" style={{ flexWrap: "wrap" }}>
              <StatCard label="Chunks indexed" value={vec.data.count.toLocaleString()} />
              <StatCard label="Backend" value={vec.data.backend} />
              <StatCard label="Collection" value={vec.data.collection} />
            </div>
          )}
        </Async>
      </div>

      <VectorProbePanel />

      <div className="card col" style={{ marginTop: 16 }}>
        <strong>Time-series store</strong>
        <Async q={ts}>
          {ts.data &&
            (ts.data.available ? (
              <>
                <div className="row" style={{ flexWrap: "wrap" }}>
                  <StatCard
                    label="Observations"
                    value={ts.data.count.toLocaleString()}
                  />
                  <StatCard label="Distinct sources" value={ts.data.sources.length} />
                </div>
                <div className="col" style={{ gap: 6 }}>
                  <span className="faint" style={{ fontSize: 11 }}>
                    SOURCES
                  </span>
                  <div className="tag-list">
                    {ts.data.sources.map((s) => (
                      <span className="chip" key={s} style={{ cursor: "default" }}>
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <div className="callout warn">
                No time-series store at <code>{ts.data.path}</code>. Build the index to
                populate it.
              </div>
            ))}
        </Async>
      </div>

      {ts.data?.available && <TimeseriesQueryPanel store={ts.data} />}
    </div>
  );
}

// Raw pre-rerank vector search — inspect what retrieval "sees" (fused dense+sparse
// candidates + scores) before the cross-encoder rerank and gating.
function VectorProbePanel() {
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(10);
  const probe = useVectorProbe();

  function run() {
    const q = query.trim();
    if (!q || probe.isPending) return;
    probe.mutate({ query: q, limit });
  }

  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Vector probe</strong>
      <span className="faint" style={{ fontSize: 12 }}>
        Raw fused dense+sparse candidates, pre-rerank — what the retriever pools before
        the cross-encoder rerank.
      </span>
      <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
        <input
          className="input"
          placeholder="probe query, e.g. severely adverse unemployment path"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          style={{ flex: 1, minWidth: 240 }}
        />
        <label className="row faint" style={{ fontSize: 13, gap: 6 }}>
          top
          <input
            className="input"
            type="number"
            min={1}
            max={50}
            value={limit}
            onChange={(e) => setLimit(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            style={{ width: 64 }}
          />
        </label>
        <button className="btn primary" onClick={run} disabled={probe.isPending}>
          {probe.isPending ? "Probing…" : "Probe"}
        </button>
      </div>

      {probe.isError && (
        <div className="callout bad">{(probe.error as Error).message}</div>
      )}
      {probe.data && (
        <div className="col" style={{ gap: 6 }}>
          <span className="faint" style={{ fontSize: 12 }}>
            {probe.data.n} candidates
          </span>
          {probe.data.candidates.map((c) => (
            <div
              key={c.chunk_id}
              className="row"
              style={{ justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}
            >
              <div className="col" style={{ gap: 2, minWidth: 0 }}>
                <div className="row" style={{ flexWrap: "wrap" }}>
                  <ChunkIdChip chunkId={c.chunk_id} />
                  <span className="faint" style={{ fontSize: 12 }}>
                    {c.publisher} · {c.doc_type} · {c.release_date}
                  </span>
                </div>
                <span className="faint" style={{ fontSize: 12 }}>
                  {c.snippet}
                </span>
              </div>
              <ScoreBar value={c.score} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Query builder over the SQLite time-series store (source / variable / scenario).
function TimeseriesQueryPanel({ store }: { store: TimeseriesStoreDto }) {
  const [source, setSource] = useState("");
  const [variable, setVariable] = useState("");
  const [scenario, setScenario] = useState("");
  const [submitted, setSubmitted] = useState<TsQueryParams | null>(null);
  const q = useTimeseriesQuery(submitted);

  function run() {
    setSubmitted({
      source: source || undefined,
      variable: variable || undefined,
      scenario: scenario || undefined,
      limit: 200,
    });
  }

  const cols = q.data?.rows.length
    ? Object.keys(q.data.rows[0]).filter((k) => k !== "id")
    : [];

  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Time-series query</strong>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <select className="input" value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">any source</option>
          {store.sources.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <input
          className="input"
          placeholder="variable (exact)"
          value={variable}
          onChange={(e) => setVariable(e.target.value)}
          style={{ minWidth: 160 }}
        />
        <input
          className="input"
          placeholder="scenario (exact)"
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          style={{ minWidth: 140 }}
        />
        <button className="btn primary" onClick={run}>Query</button>
        {q.data && q.data.rows.length > 0 && (
          <button
            className="btn"
            onClick={() => downloadCsv("timeseries_query.csv", q.data!.rows, cols)}
          >
            Export CSV
          </button>
        )}
      </div>

      {submitted && (
        <Async q={q} loadingText="Querying…">
          {q.data &&
            (q.data.rows.length === 0 ? (
              <p className="muted">No observations match this query.</p>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <span className="faint" style={{ fontSize: 12 }}>{q.data.n} rows</span>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                      {cols.map((c) => (
                        <th key={c} style={{ padding: "8px 12px", fontSize: 12 }}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {q.data.rows.map((r, i) => (
                      <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                        {cols.map((c) => (
                          <td key={c} style={{ padding: "8px 12px", fontSize: 13, whiteSpace: "nowrap" }}>
                            {fmtCell(r[c])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
        </Async>
      )}
    </div>
  );
}

function fmtCell(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return String(v);
  return String(v);
}
