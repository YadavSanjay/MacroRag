import { useCorpusDocument } from "@/api/corpus";
import { Async } from "@/components/Async";
import { Drawer } from "@/components/Drawer";

// Document detail: metadata, chunk bodies (read straight from the processed JSON),
// and a numeric-series summary. Chunk text is shown inline (no /citations round-trip).
export function DocumentDrawer({
  docId,
  onClose,
}: {
  docId: string;
  onClose: () => void;
}) {
  const q = useCorpusDocument(docId);

  return (
    <Drawer title={<span className="chip" style={{ cursor: "default" }}>{docId}</span>} onClose={onClose}>
      <Async q={q} loadingText="Loading document…">
        {q.data && (
          <div className="col">
            <div>
              <div style={{ fontSize: 16, fontWeight: 700 }}>{q.data.title}</div>
              <div className="muted">{q.data.publication}</div>
            </div>

            <div className="row" style={{ flexWrap: "wrap" }}>
              <span className="badge">{q.data.publisher}</span>
              <span className="badge">{q.data.doc_type}</span>
              {q.data.release_date && <span className="badge">{q.data.release_date}</span>}
              {q.data.tier != null && <span className="badge">tier {q.data.tier}</span>}
              {q.data.bucket && <span className="badge">bucket {q.data.bucket}</span>}
              {q.data.url && (
                <a href={q.data.url} target="_blank" rel="noreferrer">source ↗</a>
              )}
            </div>

            {(q.data.entity_tags.length > 0 || q.data.factor_tags.length > 0) && (
              <div className="tag-list">
                {q.data.factor_tags.map((t) => (
                  <span key={`f-${t}`} className="chip" style={{ cursor: "default" }}>{t}</span>
                ))}
                {q.data.entity_tags.map((t) => (
                  <span key={`e-${t}`} className="chip" style={{ cursor: "default" }}>{t}</span>
                ))}
              </div>
            )}

            {q.data.timeseries.n_rows > 0 && (
              <div className="callout">
                <strong>{q.data.timeseries.n_rows} numeric rows</strong>
                <div className="faint" style={{ fontSize: 12 }}>
                  sources: {q.data.timeseries.sources.join(", ") || "—"} · variables:{" "}
                  {q.data.timeseries.variables.join(", ") || "—"}
                </div>
              </div>
            )}

            <div className="col" style={{ gap: 10, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              <span className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>
                Chunks ({q.data.chunks.length})
              </span>
              {q.data.chunks.map((ch) => (
                <div key={ch.chunk_id} className="card" style={{ padding: 12 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="chip" style={{ cursor: "default" }}>{ch.chunk_id}</span>
                    <span className="faint" style={{ fontSize: 11 }}>
                      {ch.section ? `${ch.section} · ` : ""}{ch.n_chars} chars
                    </span>
                  </div>
                  <div style={{ whiteSpace: "pre-wrap", marginTop: 8, lineHeight: 1.6 }}>
                    {ch.text}
                  </div>
                </div>
              ))}
              {q.data.chunks.length === 0 && (
                <p className="faint">Numeric document — no narrative chunks.</p>
              )}
            </div>
          </div>
        )}
      </Async>
    </Drawer>
  );
}
