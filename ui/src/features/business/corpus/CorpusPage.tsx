import { useState } from "react";
import {
  activeCorpusFilterCount,
  emptyCorpusFilters,
  useCorpusDocuments,
  useCorpusFacets,
  useCorpusSeries,
  type CorpusFilters,
} from "@/api/corpus";
import { Async, StatCard } from "@/components/Async";
import { Table, Td, Th } from "@/components/Table";
import { downloadCsv } from "@/lib/download";
import { FacetRail } from "./FacetRail";
import { DocumentDrawer } from "./DocumentDrawer";

const DOC_CSV_COLUMNS = [
  "doc_id", "title", "publisher", "publication", "release_date", "doc_type",
  "tier", "bucket", "n_chunks", "n_timeseries",
];

type Tab = "documents" | "series";

// P3 — Corpus Explorer: facet-driven browse of documents + the numeric series catalog.
export function CorpusPage() {
  const [filters, setFilters] = useState<CorpusFilters>(emptyCorpusFilters());
  const [tab, setTab] = useState<Tab>("documents");
  const [docId, setDocId] = useState<string | null>(null);

  const facets = useCorpusFacets();
  const nActive = activeCorpusFilterCount(filters);

  return (
    <div className="content">
      <h1 className="page-title">Corpus</h1>
      <p className="page-sub">
        Browse the ingested knowledge base by facet, inspect documents and chunks, and
        explore the numeric series catalog.
      </p>

      <Async q={facets}>
        {facets.data && (
          <>
            <div className="row" style={{ flexWrap: "wrap", marginBottom: 16 }}>
              <StatCard label="Documents" value={facets.data.n_documents.toLocaleString()} />
              <StatCard label="Chunks" value={facets.data.n_chunks.toLocaleString()} />
              <StatCard label="Numeric docs" value={facets.data.n_timeseries_docs.toLocaleString()} />
              <StatCard
                label="Date range"
                value={
                  facets.data.date_range.min
                    ? `${facets.data.date_range.min} → ${facets.data.date_range.max}`
                    : "—"
                }
              />
            </div>

            <div className="row" style={{ gap: 8, marginBottom: 12 }}>
              <button className={`btn ${tab === "documents" ? "primary" : ""}`} onClick={() => setTab("documents")}>
                Documents
              </button>
              <button className={`btn ${tab === "series" ? "primary" : ""}`} onClick={() => setTab("series")}>
                Series catalog
              </button>
            </div>

            {tab === "documents" ? (
              <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 20 }}>
                <div>
                  <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
                    <strong>Filters{nActive ? ` (${nActive})` : ""}</strong>
                    {nActive > 0 && (
                      <button className="chip" onClick={() => setFilters(emptyCorpusFilters())}>
                        clear
                      </button>
                    )}
                  </div>
                  <FacetRail facets={facets.data} filters={filters} onChange={setFilters} />
                </div>
                <DocumentsTable filters={filters} onChange={setFilters} onOpen={setDocId} />
              </div>
            ) : (
              <SeriesView />
            )}
          </>
        )}
      </Async>

      {docId && <DocumentDrawer docId={docId} onClose={() => setDocId(null)} />}
    </div>
  );
}

function DocumentsTable({
  filters,
  onChange,
  onOpen,
}: {
  filters: CorpusFilters;
  onChange: (f: CorpusFilters) => void;
  onOpen: (id: string) => void;
}) {
  const q = useCorpusDocuments(filters);
  const start = filters.offset + 1;

  return (
    <Async q={q} loadingText="Loading documents…">
      {q.data && (
        <div className="col">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="faint">
              {q.data.total === 0
                ? "no matches"
                : `${start}–${Math.min(filters.offset + filters.limit, q.data.total)} of ${q.data.total}`}
            </span>
            <div className="row" style={{ gap: 6 }}>
              <button
                className="btn"
                disabled={q.data.documents.length === 0}
                title="download the current page as CSV"
                onClick={() =>
                  downloadCsv(
                    "corpus_documents.csv",
                    q.data!.documents as unknown as Record<string, unknown>[],
                    DOC_CSV_COLUMNS,
                  )
                }
              >
                Export CSV
              </button>
              <button
                className="btn"
                disabled={filters.offset === 0}
                onClick={() => onChange({ ...filters, offset: Math.max(0, filters.offset - filters.limit) })}
              >
                ‹ Prev
              </button>
              <button
                className="btn"
                disabled={filters.offset + filters.limit >= q.data.total}
                onClick={() => onChange({ ...filters, offset: filters.offset + filters.limit })}
              >
                Next ›
              </button>
            </div>
          </div>

          <Table>
              <thead>
                <tr>
                  <Th>Title</Th>
                  <Th>Publisher</Th>
                  <Th>Type</Th>
                  <Th>Date</Th>
                  <Th>Chunks</Th>
                </tr>
              </thead>
              <tbody>
                {q.data.documents.map((d) => (
                  <tr key={d.doc_id} className="clickable" onClick={() => onOpen(d.doc_id)}>
                    <Td>
                      <span style={{ display: "block", maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {d.title}
                      </span>
                    </Td>
                    <Td className="faint">{d.publisher}</Td>
                    <Td><code>{d.doc_type}</code></Td>
                    <Td className="faint">{d.release_date ?? "—"}</Td>
                    <Td>{d.n_chunks || (d.n_timeseries ? `${d.n_timeseries} rows` : 0)}</Td>
                  </tr>
                ))}
                {q.data.documents.length === 0 && (
                  <tr><Td className="muted">No documents match these filters.</Td></tr>
                )}
              </tbody>
          </Table>
        </div>
      )}
    </Async>
  );
}

function SeriesView() {
  const q = useCorpusSeries();
  return (
    <Async q={q} loadingText="Loading series…">
      {q.data && (
        <div className="col">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <StatCard label="Total observations" value={q.data.n_rows_total.toLocaleString()} />
            <button
              className="btn"
              disabled={q.data.sources.length === 0}
              onClick={() =>
                downloadCsv(
                  "corpus_series.csv",
                  q.data!.sources.map((s) => ({
                    source: s.source,
                    n_rows: s.n_rows,
                    variables: s.variables.join("; "),
                    publishers: s.publishers.join("; "),
                  })),
                  ["source", "n_rows", "variables", "publishers"],
                )
              }
            >
              Export CSV
            </button>
          </div>
          <Table>
              <thead>
                <tr>
                  <Th>Source</Th>
                  <Th>Rows</Th>
                  <Th>Variables</Th>
                  <Th>Publishers</Th>
                </tr>
              </thead>
              <tbody>
                {q.data.sources.map((s) => (
                  <tr key={s.source}>
                    <Td><code>{s.source}</code></Td>
                    <Td>{s.n_rows}</Td>
                    <Td className="faint">{s.variables.join(", ") || "—"}</Td>
                    <Td className="faint">{s.publishers.join(", ") || "—"}</Td>
                  </tr>
                ))}
                {q.data.sources.length === 0 && (
                  <tr><Td className="muted">No numeric series in the corpus.</Td></tr>
                )}
              </tbody>
          </Table>
        </div>
      )}
    </Async>
  );
}
