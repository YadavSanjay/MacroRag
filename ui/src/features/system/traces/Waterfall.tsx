// Stage-timing waterfall for a single trace's timings_ms map. Segments are sized
// proportionally to each stage's duration (stages run sequentially in the pipeline).

const PALETTE = [
  "#4c8dff",
  "#3fb950",
  "#d29922",
  "#a371f7",
  "#f778ba",
  "#56d4dd",
  "#f85149",
  "#8b949e",
];

export function Waterfall({ timings }: { timings: Record<string, number> }) {
  const entries = Object.entries(timings).filter(([, v]) => Number(v) > 0);
  if (entries.length === 0) {
    return <p className="faint">No stage timings recorded for this trace.</p>;
  }
  const total = entries.reduce((a, [, v]) => a + Number(v), 0);

  return (
    <div className="col" style={{ gap: 10 }}>
      <div style={{ display: "flex", height: 22, borderRadius: 5, overflow: "hidden" }}>
        {entries.map(([k, v], i) => (
          <span
            key={k}
            title={`${k}: ${v} ms`}
            style={{
              width: `${(Number(v) / total) * 100}%`,
              background: PALETTE[i % PALETTE.length],
            }}
          />
        ))}
      </div>
      <div className="col" style={{ gap: 4 }}>
        {entries.map(([k, v], i) => (
          <div key={k} className="row" style={{ gap: 8 }}>
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: 2,
                background: PALETTE[i % PALETTE.length],
              }}
            />
            <code style={{ width: 120 }}>{k}</code>
            <span className="faint">{Number(v).toFixed(1)} ms</span>
            <span className="faint">· {((Number(v) / total) * 100).toFixed(0)}%</span>
          </div>
        ))}
        <div className="row" style={{ gap: 8, marginTop: 4 }}>
          <span style={{ width: 10 }} />
          <code style={{ width: 120, fontWeight: 700 }}>total</code>
          <span style={{ fontWeight: 700 }}>{total.toFixed(1)} ms</span>
        </div>
      </div>
    </div>
  );
}
