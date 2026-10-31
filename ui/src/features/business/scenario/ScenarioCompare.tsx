import type { ShockField } from "@/api/types";
import { orderShockFields, shockSpec } from "@/lib/shockVocab";

export interface Pinned {
  label: string;
  prompt: string;
  vector: Record<string, ShockField>;
}

// Side-by-side diff of two synthesized shock vectors. Delta is shown only when
// both sides are present and share a unit (mixing bp/pct/pts is not comparable).
export function ScenarioCompare({
  a,
  b,
  onClear,
}: {
  a: Pinned;
  b: Pinned;
  onClear: () => void;
}) {
  const fields = orderShockFields([
    ...Object.keys(a.vector),
    ...Object.keys(b.vector),
  ].filter((v, i, arr) => arr.indexOf(v) === i));

  return (
    <div className="card col">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Compare</strong>
        <button className="chip" onClick={onClear}>clear</button>
      </div>
      <div className="row" style={{ gap: 24 }}>
        <span className="faint">A — {a.prompt || a.label}</span>
        <span className="faint">B — {b.prompt || b.label}</span>
      </div>

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
            <th style={th}>Field</th>
            <th style={th}>A</th>
            <th style={th}>B</th>
            <th style={th}>Δ (B−A)</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => {
            const fa = a.vector[f];
            const fb = b.vector[f];
            return (
              <tr key={f} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={td}>
                  <code style={{ fontWeight: 700 }}>{f}</code>
                  <span className="faint" style={{ fontSize: 11 }}>
                    {" "}{shockSpec(f)?.preferred ?? fa?.unit ?? fb?.unit ?? ""}
                  </span>
                </td>
                <td style={td}>{cell(fa)}</td>
                <td style={td}>{cell(fb)}</td>
                <td style={td}>{delta(fa, fb)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function cell(f: ShockField | undefined) {
  if (!f) return <span className="faint">—</span>;
  return (
    <span>
      {fmt(f.value)} <span className="faint" style={{ fontSize: 11 }}>{f.unit}</span>
    </span>
  );
}

function delta(a: ShockField | undefined, b: ShockField | undefined) {
  if (!a || !b) return <span className="faint">—</span>;
  if (a.unit !== b.unit) return <span className="faint" title="units differ">n/a</span>;
  const d = b.value - a.value;
  const color = d > 0 ? "var(--good)" : d < 0 ? "var(--bad)" : "var(--text-dim)";
  return <span style={{ color, fontWeight: 600 }}>{fmt(d)}</span>;
}

function fmt(v: number): string {
  return (v > 0 ? "+" : "") + v;
}

const th: React.CSSProperties = { padding: "6px 10px", fontWeight: 600, fontSize: 11 };
const td: React.CSSProperties = { padding: "6px 10px", fontSize: 13 };
