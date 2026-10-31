import type { ChunkBrief } from "@/api/types";
import { useCitations } from "@/components/Citations";
import { ScoreBar } from "@/components/ScoreBar";

// Retrieved-evidence list shared by Ask (AnswerView) and Factors. Chunks the
// answer actually cited carry their reader-facing number ([1], [2], …) from
// `cites` and are highlighted; the rest are retrieved-but-uncited context. The
// whole row opens the source drawer. Raw rerank scores live in Traces, not here.
export function EvidenceRail({
  chunks,
  cites,
  title = "Sources",
}: {
  chunks: ChunkBrief[];
  cites: Map<string, number>;
  title?: string;
}) {
  const { open } = useCitations();
  return (
    <div className="card col">
      <strong>
        {title} ({chunks.length})
      </strong>
      <div className="col" style={{ gap: "var(--space-1)" }}>
        {chunks.map((c) => {
          const n = cites.get(c.chunk_id);
          const isCited = n != null;
          return (
            <div
              key={c.chunk_id}
              className={`evidence-row${isCited ? " cited" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => open(c.chunk_id, n)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  open(c.chunk_id, n);
                }
              }}
            >
              <div className="col" style={{ gap: "var(--space-1)", minWidth: 0 }}>
                <div className="row" style={{ flexWrap: "wrap", gap: "var(--space-2)" }}>
                  {isCited ? (
                    <span className="cite-chip">{n}</span>
                  ) : (
                    <span className="faint" style={{ fontSize: "var(--text-caption)" }}>
                      context
                    </span>
                  )}
                  <span style={{ fontWeight: 600 }}>{c.publisher}</span>
                  {c.expanded && <span className="badge warn">expanded</span>}
                </div>
                <div className="faint" style={{ fontSize: "var(--text-caption)" }}>
                  {c.doc_type} · {c.release_date}
                </div>
              </div>
              <ScoreBar value={c.rerank_score} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
