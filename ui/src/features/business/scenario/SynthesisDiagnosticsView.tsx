import type { SynthesisDiagnostics } from "@/api/types";
import { isHardCode, validationHint } from "@/lib/shockVocab";

// Renders the per-synthesize diagnostics (src/synthesis/diagnostics.py) so a user
// isn't left guessing on a thin/empty shock vector — it shows the grounding outcome,
// evidence counts, analog-grounded fields, every dropped field with its reason, and
// why each explicitly requested field is missing.
const OUTCOME_TONE: Record<string, string> = {
  grounded: "badge good",
  partial: "badge warn",
  all_dropped: "badge bad",
  llm_empty: "badge warn",
  upstream_refused: "badge bad",
  no_chunks: "badge bad",
};

const OUTCOME_LABEL: Record<string, string> = {
  grounded: "fully grounded",
  partial: "partially grounded",
  all_dropped: "all fields dropped",
  llm_empty: "model emitted no fields",
  upstream_refused: "upstream query refused",
  no_chunks: "no evidence retrieved",
};

export function SynthesisDiagnosticsView({ diag }: { diag: SynthesisDiagnostics }) {
  return (
    <div className="card col">
      <div className="row" style={{ flexWrap: "wrap", alignItems: "center" }}>
        <strong>Grounding diagnostics</strong>
        <span className={OUTCOME_TONE[diag.outcome] ?? "badge"}>
          {OUTCOME_LABEL[diag.outcome] ?? diag.outcome}
        </span>
        <div className="spacer" style={{ flex: 1 }} />
        <span className="faint" style={{ fontSize: 12 }}>
          {diag.n_chunks} chunks ({diag.n_dated_chunks} dated) · {diag.kept_fields.length}{" "}
          kept / {diag.emitted_fields.length} emitted
        </span>
      </div>

      {diag.analog_fields.length > 0 && (
        <div className="row" style={{ flexWrap: "wrap", alignItems: "center", gap: 6 }}>
          <span className="faint" style={{ fontSize: 12 }}>
            grounded via analog/proxy (down-weight vs direct):
          </span>
          {diag.analog_fields.map((f) => (
            <span key={f} className="badge warn">
              {f}
            </span>
          ))}
        </div>
      )}

      {(diag.missing_fields ?? []).length > 0 && (
        <div className="col" style={{ gap: 4 }}>
          <span className="faint" style={{ fontSize: 12, textTransform: "uppercase" }}>
            Requested but missing
          </span>
          {diag.missing_fields!.map((m) => (
            <div
              key={m.field}
              className="row"
              style={{ justifyContent: "space-between", gap: 12, fontSize: 13 }}
            >
              <code style={{ fontWeight: 700 }}>{m.field}</code>
              <span className="muted">
                {m.reason === "dropped" ? "dropped" : "not emitted"}
                {m.codes.length > 0 && <span className="faint"> · {m.codes.join(", ")}</span>}
                {!m.card_field && (
                  <span
                    className="faint"
                    title="no market-episode card measures this field, so it can only come from prose"
                  >
                    {" "}· prose only
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {diag.dropped.length > 0 && (
        <div className="col" style={{ gap: 4 }}>
          <span className="faint" style={{ fontSize: 12, textTransform: "uppercase" }}>
            Dropped fields
          </span>
          {diag.dropped.map((d, i) => (
            <div
              key={`${d.field}-${i}`}
              className="row"
              style={{ justifyContent: "space-between", gap: 12, fontSize: 13 }}
            >
              <code style={{ fontWeight: 700 }}>{d.field}</code>
              <span className="muted">
                {d.reason}
                {Array.isArray(d.codes) && d.codes.length > 0 && (
                  <span className="faint"> ({(d.codes as string[]).join(", ")})</span>
                )}{" "}
                <span className="faint">· {d.stage}</span>
              </span>
            </div>
          ))}
        </div>
      )}

      {diag.validation.length > 0 && (
        <div className="callout warn" style={{ fontSize: 13 }}>
          <strong>Validation warnings:</strong>
          <ul style={{ margin: "6px 0 0" }}>
            {diag.validation.map((v, i) => (
              <li key={i}>
                {isHardCode(String(v.code)) && <span className="badge bad">hard</span>}{" "}
                {fmtWarning(v)}
                {validationHint(String(v.code)) && (
                  <span className="faint"> — {validationHint(String(v.code))}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function fmtWarning(v: Record<string, unknown>): string {
  const code = String(v.code ?? "warning");
  const rest = Object.entries(v)
    .filter(([k]) => k !== "code")
    .map(([k, val]) => `${k}=${JSON.stringify(val)}`)
    .join(", ");
  return rest ? `${code} (${rest})` : code;
}
