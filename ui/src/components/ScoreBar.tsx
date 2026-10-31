// Renders a 0..1 relevance score (rerank_score / fused score) as a small bar.
// Tolerates null/undefined (e.g. expanded neighbour chunks have no rerank score),
// rendering a muted placeholder instead of crashing.
export function ScoreBar({ value }: { value: number | null | undefined }) {
  if (value == null || !Number.isFinite(value)) {
    return <span className="scorebar-val faint" title="no relevance score">—</span>;
  }
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span className="scorebar" title={`score ${value.toFixed(4)}`}>
      <span className="scorebar-track">
        <span className="scorebar-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="scorebar-val">{value.toFixed(2)}</span>
    </span>
  );
}
