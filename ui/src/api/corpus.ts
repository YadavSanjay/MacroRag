// Types + TanStack hooks for the corpus browse surface (backend: src/api/corpus.py).

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { apiGet } from "./client";

export interface FacetCount {
  value: string | number;
  count: number;
}

export interface CorpusFacets {
  n_documents: number;
  n_chunks: number;
  n_timeseries_docs: number;
  date_range: { min: string | null; max: string | null };
  tiers: FacetCount[];
  buckets: FacetCount[];
  doc_types: FacetCount[];
  publishers: FacetCount[];
  entity_tags: FacetCount[];
  factor_tags: FacetCount[];
}

export interface DocSummary {
  doc_id: string;
  source_id: string;
  title: string;
  publisher: string;
  publication: string;
  release_date: string | null;
  doc_type: string;
  tier: number | null;
  bucket: string | null;
  n_chunks: number;
  n_timeseries: number;
  entity_tags: string[];
  factor_tags: string[];
}

export interface DocumentsResponse {
  total: number;
  limit: number;
  offset: number;
  documents: DocSummary[];
}

export interface DocChunk {
  chunk_id: string;
  seq: number;
  section: string | null;
  n_chars: number;
  entity_tags: string[];
  factor_tags: string[];
  text: string;
}

export interface DocDetail extends DocSummary {
  url: string | null;
  chunks: DocChunk[];
  timeseries: {
    n_rows: number;
    sources: string[];
    variables: string[];
    sample: Record<string, unknown>[];
  };
}

export interface SeriesCatalog {
  n_rows_total: number;
  sources: {
    source: string;
    n_rows: number;
    variables: string[];
    publishers: string[];
  }[];
}

export interface CorpusFilters {
  tiers: number[];
  doc_types: string[];
  publishers: string[];
  buckets: string[];
  entity_tags: string[];
  factor_tags: string[];
  q: string;
  date_from?: string | null;
  date_to?: string | null;
  limit: number;
  offset: number;
}

export function emptyCorpusFilters(): CorpusFilters {
  return {
    tiers: [],
    doc_types: [],
    publishers: [],
    buckets: [],
    entity_tags: [],
    factor_tags: [],
    q: "",
    date_from: null,
    date_to: null,
    limit: 50,
    offset: 0,
  };
}

// Backend uses singular repeated query params (tier, doc_type, …).
function buildParams(f: CorpusFilters): string {
  const p = new URLSearchParams();
  f.tiers.forEach((v) => p.append("tier", String(v)));
  f.doc_types.forEach((v) => p.append("doc_type", v));
  f.publishers.forEach((v) => p.append("publisher", v));
  f.buckets.forEach((v) => p.append("bucket", v));
  f.entity_tags.forEach((v) => p.append("entity_tag", v));
  f.factor_tags.forEach((v) => p.append("factor_tag", v));
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.date_from) p.set("date_from", f.date_from);
  if (f.date_to) p.set("date_to", f.date_to);
  p.set("limit", String(f.limit));
  p.set("offset", String(f.offset));
  return p.toString();
}

export function activeCorpusFilterCount(f: CorpusFilters): number {
  return (
    f.tiers.length +
    f.doc_types.length +
    f.publishers.length +
    f.buckets.length +
    f.entity_tags.length +
    f.factor_tags.length +
    (f.q.trim() ? 1 : 0) +
    (f.date_from ? 1 : 0) +
    (f.date_to ? 1 : 0)
  );
}

export function useCorpusFacets() {
  return useQuery({
    queryKey: ["corpus", "facets"],
    queryFn: () => apiGet<CorpusFacets>("/corpus/facets"),
  });
}

export function useCorpusDocuments(filters: CorpusFilters) {
  return useQuery({
    queryKey: ["corpus", "documents", filters],
    queryFn: () => apiGet<DocumentsResponse>(`/corpus/documents?${buildParams(filters)}`),
    placeholderData: keepPreviousData,
  });
}

export function useCorpusDocument(docId: string | null) {
  return useQuery({
    queryKey: ["corpus", "document", docId],
    queryFn: () => apiGet<DocDetail>(`/corpus/documents/${encodeURIComponent(docId!)}`),
    enabled: !!docId,
  });
}

export function useCorpusSeries() {
  return useQuery({
    queryKey: ["corpus", "series"],
    queryFn: () => apiGet<SeriesCatalog>("/corpus/series"),
  });
}
