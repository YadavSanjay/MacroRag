import type { QueryResultDto } from "@/api/types";

// A derived (not backend-authored) trust signal, stated as one calm line rather
// than a wall of scores. Grounding = the top rerank score among the chunks the
// answer actually cited (kept internal, mapped to a plain-language level).
// Verifier drops / corpus gaps are surfaced quietly and honestly — never as an
// alarm, since a verifier drop means the guard worked. Raw scores live in Traces.
export function ConfidenceReadout({ result }: { result: QueryResultDto }) {
  const cited = new Set(result.used_chunk_ids);
  const scores = result.chunks
    .filter((c) => cited.has(c.chunk_id) && c.rerank_score != null)
    .map((c) => c.rerank_score as number);
  const nSources = cited.size;
  const top = scores.length ? Math.max(...scores) : null;

  const dropped = result.unsupported.length;
  const gaps = result.unanswered.length;

  // strong grounding + full coverage => high; otherwise limited; no sources => none
  const level: "high" | "limited" | "none" =
    nSources === 0
      ? "none"
      : top != null && top >= 0.6 && gaps === 0
        ? "high"
        : "limited";

  const grounding =
    top == null ? "n/a" : top >= 0.6 ? "strong" : top >= 0.35 ? "moderate" : "weak";
  const derivedTitle =
    top == null
      ? "Derived in the UI from cited-evidence relevance + verifier output — not a model-emitted confidence score. Full scores are in Traces."
      : `Top source relevance ${top.toFixed(2)} (${grounding}). Derived in the UI from cited-evidence relevance + verifier output — not a model-emitted confidence score. Full scores are in Traces.`;

  const tone = level === "high" ? "good" : level === "none" ? "bad" : "warn";
  const headline =
    level === "none"
      ? "No sources grounded this answer"
      : `Answered from ${nSources} source${nSources > 1 ? "s" : ""}`;
  const qualifier =
    level === "limited" ? " — limited evidence, verify before relying on this" : "";

  return (
    <div
      className="row row-highlight"
      style={{ flexWrap: "wrap", gap: "var(--space-3)", alignItems: "center" }}
    >
      <span className={`badge ${tone}`}>● {headline}</span>
      {qualifier && (
        <span className="faint" style={{ fontSize: "var(--text-body-sm)" }}>
          {qualifier.replace(/^ — /, "")}
        </span>
      )}
      {dropped > 0 && (
        <span
          className="faint"
          style={{ fontSize: "var(--text-body-sm)" }}
          title="The faithfulness verifier removed claims the sources did not support — the answer you see is the filtered result."
        >
          verifier removed {dropped} claim{dropped > 1 ? "s" : ""}
        </span>
      )}
      {gaps > 0 && (
        <span className="faint" style={{ fontSize: "var(--text-body-sm)" }}>
          {gaps} question{gaps > 1 ? "s" : ""} not covered
        </span>
      )}
      <div className="spacer" style={{ flex: 1 }} />
      <span
        className="faint"
        style={{ fontSize: "var(--text-caption)" }}
        title={derivedTitle}
      >
        derived ⓘ
      </span>
    </div>
  );
}
