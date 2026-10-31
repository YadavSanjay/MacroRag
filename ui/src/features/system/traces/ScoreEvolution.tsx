import type { TraceRetrieved } from "@/api/admin";

// Score evolution across the retained set: how each chunk's rank shifts from the
// fused (RRF) score to the cross-encoder rerank score. Computed from the per-chunk
// scores already recorded in the trace (the retained chunks, not the full candidate
// pool — full per-stage capture would need backend trace enrichment).

interface Row {
  chunk_id: string;
  source_id: string | null;
  fusedRank: number;
  rerankRank: number | null;
  score: number;
  rerank_score: number | null;
  delta: number | null; // positive = moved up under rerank
}

function evolution(retrieved: TraceRetrieved[]): Row[] {
  const byFused = [...retrieved].sort((a, b) => b.score - a.score);
  const fusedRank = new Map(byFused.map((c, i) => [c.chunk_id, i]));

  const withRr = retrieved.filter((c) => c.rerank_score != null);
  const byRr = [...withRr].sort((a, b) => (b.rerank_score ?? 0) - (a.rerank_score ?? 0));
  const rerankRank = new Map(byRr.map((c, i) => [c.chunk_id, i]));

  return retrieved
    .map((c) => {
      const fr = fusedRank.get(c.chunk_id) ?? 0;
      const rr = rerankRank.has(c.chunk_id) ? rerankRank.get(c.chunk_id)! : null;
      return {
        chunk_id: c.chunk_id,
        source_id: c.source_id,
        fusedRank: fr,
        rerankRank: rr,
        score: c.score,
        rerank_score: c.rerank_score,
        delta: rr == null ? null : fr - rr,
      };
    })
    .sort((a, b) => (a.rerankRank ?? 999) - (b.rerankRank ?? 999));
}

export function ScoreEvolution({ retrieved }: { retrieved: TraceRetrieved[] }) {
  const rows = evolution(retrieved);
  const hasRerank = rows.some((r) => r.rerankRank != null);
  if (!hasRerank) {
    return <p className="faint">No rerank scores recorded for this trace.</p>;
  }

  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
          <th style={th}>Chunk</th>
          <th style={th}>Fused</th>
          <th style={th}>Rerank</th>
          <th style={th}>Move</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.chunk_id} style={{ borderTop: "1px solid var(--border)" }}>
            <td style={td}>
              <code>{r.chunk_id}</code>
              {r.source_id && <span className="faint" style={{ fontSize: 11 }}> · {r.source_id}</span>}
            </td>
            <td style={td} className="faint">
              #{r.fusedRank + 1} ({r.score.toFixed(3)})
            </td>
            <td style={td} className="faint">
              {r.rerankRank == null
                ? "—"
                : `#${r.rerankRank + 1} (${(r.rerank_score ?? 0).toFixed(3)})`}
            </td>
            <td style={td}>
              <Move delta={r.delta} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Move({ delta }: { delta: number | null }) {
  if (delta == null) return <span className="faint">—</span>;
  if (delta === 0) return <span className="faint">·</span>;
  const up = delta > 0;
  return (
    <span style={{ color: up ? "var(--good)" : "var(--bad)", fontWeight: 600 }}>
      {up ? "▲" : "▼"} {Math.abs(delta)}
    </span>
  );
}

const th: React.CSSProperties = { padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const td: React.CSSProperties = { padding: "6px 10px", fontSize: 12 };
