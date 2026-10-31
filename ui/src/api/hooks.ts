import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost, authHeaders } from "./client";
import { postSse } from "@/lib/sse";
import type {
  CitationDto,
  FactorRequest,
  FactorsResultDto,
  FeedbackRequest,
  HealthDto,
  QueryRequest,
  QueryResultDto,
  RetrievalRoute,
  StreamEvent,
  SynthesisSchemaDto,
} from "./types";

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet<HealthDto>("/healthz"),
    refetchInterval: 30_000,
  });
}

export function useCitation(chunkId: string | null) {
  return useQuery({
    queryKey: ["citation", chunkId],
    queryFn: () => apiGet<CitationDto>(`/citations/${encodeURIComponent(chunkId!)}`),
    enabled: !!chunkId,
    staleTime: 5 * 60_000,
  });
}

// Batch-hydrate many cited chunks in ONE request (POST /citations) and seed the
// per-chunk citation cache, so opening any of their drawers is instant instead of
// an N× round-trip. Used by answers / factor lists / shock vectors that cite a lot.
export function useCitationPrefetch(chunkIds: string[]) {
  const qc = useQueryClient();
  const ids = [...new Set(chunkIds.filter(Boolean))].sort();
  const q = useQuery({
    queryKey: ["citations", "batch", ids.join(",")],
    queryFn: () =>
      apiPost<{ citations: CitationDto[]; missing: string[] }>("/citations", {
        chunk_ids: ids,
      }),
    enabled: ids.length > 0,
    staleTime: 5 * 60_000,
  });
  useEffect(() => {
    if (!q.data) return;
    for (const c of q.data.citations) qc.setQueryData(["citation", c.chunk_id], c);
  }, [q.data, qc]);
}

export function useFeedback() {
  return useMutation({
    mutationFn: (req: FeedbackRequest) =>
      apiPost<{ status: string }>("/feedback", req),
  });
}

// Non-streaming query (used by Scenario Studio / synthesize mode).
export function useQueryAnswer() {
  return useMutation({
    mutationFn: (req: QueryRequest) => apiPost<QueryResultDto>("/query", req),
  });
}

// Factor discovery — ranked, cited macro risk factors for a portfolio/theme.
export function useFactors() {
  return useMutation({
    mutationFn: (req: FactorRequest) => apiPost<FactorsResultDto>("/factors", req),
  });
}

// Public shock-field catalog (units + sign conventions). Cached long — it is
// static reference data. Powers Scenario Studio's legend + field picker.
export function useSynthesisSchema() {
  return useQuery({
    queryKey: ["synthesis", "schema"],
    queryFn: () => apiGet<SynthesisSchemaDto>("/synthesis/schema"),
    staleTime: 60 * 60_000,
  });
}

export interface StreamState {
  running: boolean;
  stage: string | null;
  retrieved: { n_chunks: number; route: RetrievalRoute; cached: boolean } | null;
  result: QueryResultDto | null;
  error: string | null;
}

const INITIAL: StreamState = {
  running: false,
  stage: null,
  retrieved: null,
  result: null,
  error: null,
};

// Streaming query hook for the Ask console. Wires /query/stream SSE frames into
// React state, with abort support.
export function useStreamingQuery() {
  const [state, setState] = useState<StreamState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(async (req: QueryRequest) => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setState({ ...INITIAL, running: true });

    try {
      await postSse({
        path: "/api/query/stream",
        body: req,
        headers: authHeaders(),
        signal: ctrl.signal,
        onFrame: (frame) => {
          if (!frame.data) return;
          const evt = decode(frame.event, frame.data);
          if (!evt) return;
          setState((s) => reduce(s, evt));
        },
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setState((s) => ({ ...s, running: false, error: (e as Error).message }));
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setState((s) => ({ ...s, running: false }));
  }, []);

  const reset = useCallback(() => setState(INITIAL), []);

  return { state, run, cancel, reset };
}

function decode(event: string, data: string): StreamEvent | null {
  let payload: Record<string, unknown> = {};
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  switch (event) {
    case "status":
      return { type: "status", stage: String(payload.stage ?? "") };
    case "retrieved":
      return {
        type: "retrieved",
        n_chunks: Number(payload.n_chunks ?? 0),
        route: payload.route as RetrievalRoute,
        cached: Boolean(payload.cached),
      };
    case "answer":
      return { type: "answer", result: payload as unknown as QueryResultDto };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}

function reduce(s: StreamState, evt: StreamEvent): StreamState {
  switch (evt.type) {
    case "status":
      return { ...s, stage: evt.stage };
    case "retrieved":
      return {
        ...s,
        retrieved: { n_chunks: evt.n_chunks, route: evt.route, cached: evt.cached },
      };
    case "answer":
      return { ...s, result: evt.result };
    case "done":
      return { ...s, running: false, stage: null };
  }
}
