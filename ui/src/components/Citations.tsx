import { createContext, useContext, useState, type ReactNode } from "react";
import { useCitation } from "@/api/hooks";
import { Drawer } from "./Drawer";
import { ScoreBar } from "./ScoreBar";

interface OpenTarget {
  chunkId: string;
  index?: number;
}

interface CitationCtx {
  open: (chunkId: string, index?: number) => void;
}

const Ctx = createContext<CitationCtx | null>(null);

// Provider owns a single global source drawer; any citation marker anywhere opens
// it via GET /citations/{chunk_id}. The optional index is the reader-facing number
// so the drawer can title itself "Source N" instead of a raw id. Wrap the app once.
export function CitationProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<OpenTarget | null>(null);
  const open = (chunkId: string, index?: number) => setTarget({ chunkId, index });

  return (
    <Ctx.Provider value={{ open }}>
      {children}
      {target && (
        <CitationDrawer
          chunkId={target.chunkId}
          index={target.index}
          onClose={() => setTarget(null)}
        />
      )}
    </Ctx.Provider>
  );
}

export function useCitations(): CitationCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useCitations must be used within CitationProvider");
  return ctx;
}

// Inline citation marker in answer / factor text: a small numbered pill, identical
// everywhere. Opens the source drawer — never renders the raw chunk id.
export function CitationChip({
  chunkId,
  index,
}: {
  chunkId: string;
  index: number;
}) {
  const { open } = useCitations();
  return (
    <button
      className="cite-chip"
      onClick={() => open(chunkId, index)}
      title={`View source ${index}`}
      aria-label={`View source ${index}`}
    >
      {index}
    </button>
  );
}

// Advanced/debug surfaces (Traces, Stores) legitimately inspect raw chunk ids —
// this variant shows the id in mono and opens the same source drawer. Keep raw
// ids out of the business surfaces; use CitationChip (numbered) there.
export function ChunkIdChip({ chunkId }: { chunkId: string }) {
  const { open } = useCitations();
  return (
    <button
      className="chip"
      onClick={() => open(chunkId)}
      title="Inspect source chunk"
    >
      {chunkId}
    </button>
  );
}

function CitationDrawer({
  chunkId,
  index,
  onClose,
}: {
  chunkId: string;
  index?: number;
  onClose: () => void;
}) {
  const { data, isLoading, isError, error } = useCitation(chunkId);

  return (
    <Drawer title={index ? `Source ${index}` : "Source"} onClose={onClose}>
      {isLoading && <p className="muted">Loading source…</p>}
      {isError && <p className="callout bad">{(error as Error).message}</p>}
      {data && (
        <div className="col">
          <div className="row" style={{ flexWrap: "wrap" }}>
            <span className="badge">{data.publisher}</span>
            <span className="badge">{data.doc_type}</span>
            <span className="badge">{data.release_date}</span>
            {data.expanded && <span className="badge warn">expanded neighbour</span>}
          </div>
          <div className="muted">
            {data.publication}
            {data.section ? ` · ${data.section}` : ""}
          </div>
          {data.url && (
            <a href={data.url} target="_blank" rel="noreferrer">
              Open original ↗
            </a>
          )}
          <hr style={{ borderColor: "var(--border)", width: "100%" }} />
          <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{data.text}</div>
          <div className="row" style={{ gap: "var(--space-2)", alignItems: "center" }}>
            <span className="faint" style={{ fontSize: "var(--text-caption)" }}>
              relevance
            </span>
            <ScoreBar value={data.rerank_score} />
          </div>
          <div className="faint" style={{ fontSize: "var(--text-caption)", fontFamily: "var(--font-mono)" }}>
            id: {chunkId}
          </div>
        </div>
      )}
    </Drawer>
  );
}
