// Types + TanStack hooks for the read-only /admin/* system surface
// (backend: src/api/admin.py). Powers the technical "System" area.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiPost } from "./client";
import type { RetrievalRoute } from "./types";

// ---- manifest ----

export interface ManifestDto {
  pipeline_version: string;
  built_at: string;
  embedding: { model: string; dense_dim: number; grounding_prefix: boolean };
  reranker: string;
  llm: { plan: string; answer: string; complex: string };
  prompt_hash: string;
  qdrant_collection: string;
  chunks: number;
  observations: number;
  // Content address of the indexed corpus. Null on manifests written before
  // digests existed — those keep the legacy date-only world_view_id.
  corpus_digest?: string | null;
}

export interface AdminManifestDto {
  manifest: ManifestDto | null;
  stale: boolean;
  world_view_id: string | null;
  pipeline_version: string;
  prompt_hash: string;
  built: boolean;
}

// ---- build history (corpus-state provenance) ----

export interface ManifestChange {
  field: string;
  before: unknown;
  after: unknown;
}

export interface ManifestBuild extends ManifestDto {
  world_view_id: string | null;
  changes?: ManifestChange[]; // diff vs the build before it
}

export interface ManifestHistoryDto {
  total: number;
  builds: ManifestBuild[];
  current: string | null;
}

export interface ManifestVerifyDto {
  world_view_id: string;
  known: boolean;
  current: string | null;
  is_current: boolean;
  build: ManifestBuild | null;
}

// ---- config ----

export interface ActiveImpls {
  embedder: string | null;
  vector_store: string | null;
  reranker: string | null;
  analyzer: string | null;
  llm: string | null;
  timeseries_store: string | null;
  agent: string | null;
}

export interface AdminConfigDto {
  embedding: { model: string; dense_dim: number; grounding_prefix: boolean };
  vector_store: { collection: string; mode: string; quantization: boolean };
  reranker: { enabled: boolean; model: string };
  llm: {
    provider: string;
    plan_model: string;
    answer_model: string;
    complex_model: string;
    complex_routes: string[];
  };
  retrieval: {
    final_k: number;
    rerank_candidates: number;
    mmr_lambda: number;
    min_rerank_score: number;
    chunk_expansion: boolean;
    hydrate_timeseries: boolean;
    plan_execute: boolean;
    verify_faithfulness: boolean;
  };
  ranking: { time_decay: boolean; half_life_days: number };
  cache: { enabled: boolean; backend: string; semantic: boolean };
  auth: { enabled: boolean };
  observability: { otel_enabled: boolean; service_name: string };
  built: boolean;
  active_impls: ActiveImpls | null;
}

// ---- traces ----

export interface TraceRetrieved {
  chunk_id: string;
  source_id: string | null;
  score: number;
  rerank_score: number | null;
  expanded: boolean;
}

// One captured LLM call. system/user/response are only populated by the
// single-trace detail endpoint (/admin/traces/{i}); the list endpoint strips
// the bodies and keeps just the token/cost accounting.
export interface LlmCall {
  purpose: string;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  cost_usd: number | null;
  latency_s: number;
  system?: string | null;
  user?: string | null;
  response?: string | null;
}

export interface TraceUsage {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  cost_usd: number | null;
  cost_complete: boolean;
  latency_s: number;
  call_log: LlmCall[];
}

export interface TraceAnswerDetail {
  text: string;
  chunk_ids: string[];
}

export interface TraceAnswer {
  summary: string;
  details: TraceAnswerDetail[];
  unanswered: string[];
  unsupported: string[];
  used_chunk_ids: string[];
  refused: boolean;
}

export interface TraceRecord {
  i: number;
  ts?: string;
  query?: string;
  route?: RetrievalRoute;
  kind?: string; // "synthesize" for shock-vector traces (else a query trace)
  filters?: {
    factor_tags?: string[];
    entity_tags?: string[];
    tiers?: number[];
    doc_types?: string[];
    date_from?: string | null;
    date_to?: string | null;
  };
  n_branches?: number;
  retrieved?: TraceRetrieved[];
  timeseries?: string[];
  refused?: boolean;
  n_details?: number;
  n_unsupported?: number;
  answer?: TraceAnswer;
  timings_ms?: Record<string, number>;
  usage?: TraceUsage | null;
}

export interface AdminTracesDto {
  total: number;
  limit: number;
  offset: number;
  traces: TraceRecord[];
}

// ---- stores ----

export interface VectorStoreDto {
  backend: string;
  count: number;
  collection: string;
}

export interface TimeseriesStoreDto {
  available: boolean;
  path: string;
  count: number;
  sources: string[];
}

// ---- store drill-down / probes (Tier 3) ----

export interface VectorProbeCandidate {
  chunk_id: string;
  source_id: string | null;
  publisher: string;
  publication: string;
  release_date: string;
  doc_type: string;
  rerank_score: number | null;
  expanded: boolean;
  score: number; // fused dense+sparse score (pre-rerank)
  snippet: string;
}

export interface VectorProbeResult {
  query: string;
  n: number;
  candidates: VectorProbeCandidate[];
}

export function useVectorProbe() {
  return useMutation({
    mutationFn: (req: { query: string; limit?: number }) =>
      apiPost<VectorProbeResult>("/admin/stores/vector/search", req),
  });
}

export interface TsQueryParams {
  source?: string;
  variable?: string;
  scenario?: string;
  limit?: number;
}

export interface TsQueryResult {
  available: boolean;
  n: number;
  rows: Record<string, unknown>[];
}

export function useTimeseriesQuery(params: TsQueryParams | null) {
  return useQuery({
    queryKey: ["admin", "stores", "ts", "query", params],
    queryFn: () => {
      const p = new URLSearchParams();
      if (params?.source) p.set("source", params.source);
      if (params?.variable) p.set("variable", params.variable);
      if (params?.scenario) p.set("scenario", params.scenario);
      p.set("limit", String(params?.limit ?? 200));
      return apiGet<TsQueryResult>(`/admin/stores/timeseries/query?${p.toString()}`);
    },
    enabled: params != null,
  });
}

// ---- hooks ----

export function useManifest() {
  return useQuery({
    queryKey: ["admin", "manifest"],
    queryFn: () => apiGet<AdminManifestDto>("/admin/manifest"),
  });
}

export function useManifestHistory(limit = 25) {
  return useQuery({
    queryKey: ["admin", "manifest", "history", limit],
    queryFn: () =>
      apiGet<ManifestHistoryDto>(`/admin/manifest/history?limit=${limit}`),
  });
}

// Audit lookup for a world_view_id carried by an old shock vector.
export function useVerifyWorldView(worldView: string | null) {
  return useQuery({
    queryKey: ["admin", "manifest", "verify", worldView],
    queryFn: () =>
      apiGet<ManifestVerifyDto>(
        `/admin/manifest/verify?world_view=${encodeURIComponent(worldView!)}`,
      ),
    enabled: !!worldView,
  });
}

export function useAdminConfig() {
  return useQuery({
    queryKey: ["admin", "config"],
    queryFn: () => apiGet<AdminConfigDto>("/admin/config"),
  });
}

export function useTraces(limit = 100, offset = 0) {
  return useQuery({
    queryKey: ["admin", "traces", limit, offset],
    queryFn: () =>
      apiGet<AdminTracesDto>(`/admin/traces?limit=${limit}&offset=${offset}`),
    refetchInterval: 15_000,
  });
}

// Full single trace incl. each LLM call's prompt + response bodies (the list
// endpoint omits those). Fetched lazily when a trace drawer is opened.
export function useTrace(i: number | null) {
  return useQuery({
    queryKey: ["admin", "trace", i],
    queryFn: () => apiGet<TraceRecord>(`/admin/traces/${i}`),
    enabled: i != null && i >= 0,
  });
}

export function useVectorStore() {
  return useQuery({
    queryKey: ["admin", "stores", "vector"],
    queryFn: () => apiGet<VectorStoreDto>("/admin/stores/vector"),
  });
}

// ---- dependency health (Tier 2) ----

export type HealthStatus = "ok" | "degraded" | "down" | "skipped";

export interface HealthCheck {
  name: string;
  status: HealthStatus;
  detail: string;
  latency_ms: number;
}

export interface HealthChecksDto {
  status: HealthStatus;
  built: boolean;
  checks: HealthCheck[];
}

export function useHealthChecks() {
  return useQuery({
    queryKey: ["admin", "health"],
    queryFn: () => apiGet<HealthChecksDto>("/admin/health"),
    refetchInterval: 30_000,
  });
}

// ---- metrics roll-up (Tier 2) ----

export interface MetricsDto {
  n_traces: number;
  queries: {
    total: number;
    refused: number;
    refusal_rate: number;
    cached: number;
    cache_hit_rate: number;
  };
  cost: {
    total_usd: number;
    complete: boolean;
    prompt_tokens: number;
    completion_tokens: number;
    cached_tokens: number;
    total_tokens: number;
  };
  latency_ms: { avg: number; p50: number; p95: number };
  by_route: {
    route: string;
    n: number;
    cost_usd: number;
    avg_latency_ms: number;
    refused: number;
  }[];
  by_model: { model: string; calls: number; total_tokens: number; cost_usd: number }[];
  daily: { date: string; n: number; cost_usd: number; avg_latency_ms: number }[];
}

export function useMetrics() {
  return useQuery({
    queryKey: ["admin", "metrics"],
    queryFn: () => apiGet<MetricsDto>("/admin/metrics"),
    refetchInterval: 30_000,
  });
}

export function useTimeseriesStore() {
  return useQuery({
    queryKey: ["admin", "stores", "timeseries"],
    queryFn: () => apiGet<TimeseriesStoreDto>("/admin/stores/timeseries"),
  });
}

// ---- jobs ----

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface JobSummary {
  id: string;
  type: string;
  params: Record<string, unknown>;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
  n_logs: number;
}

export interface JobLog {
  ts: string;
  msg: string;
}

export interface JobDetail extends Omit<JobSummary, "n_logs"> {
  logs: JobLog[];
}

export function isTerminal(s?: JobStatus): boolean {
  return s === "succeeded" || s === "failed" || s === "cancelled";
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      apiPost<JobSummary>(`/admin/jobs/${encodeURIComponent(jobId)}/cancel`, {}),
    onSuccess: (_d, jobId) => {
      qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
      qc.invalidateQueries({ queryKey: ["admin", "job", jobId] });
    },
  });
}

export function useJobs() {
  return useQuery({
    queryKey: ["admin", "jobs"],
    queryFn: () => apiGet<{ jobs: JobSummary[] }>("/admin/jobs"),
    refetchInterval: 5_000,
  });
}

// Poll a single job's detail (logs + status) while it is still running.
export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ["admin", "job", jobId],
    queryFn: () => apiGet<JobDetail>(`/admin/jobs/${encodeURIComponent(jobId!)}`),
    enabled: !!jobId,
    refetchInterval: (query) =>
      isTerminal(query.state.data?.status) ? false : 1000,
  });
}

// How an index job runs: a full rebuild (default), an append, or the incremental refresh
// (embed only new + changed chunks, delete departed ones) — optionally as a dry-run plan.
export interface IndexMode {
  append?: boolean;
  incremental?: boolean;
  dry_run?: boolean;
}

export function useTriggerIndex() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mode: IndexMode) =>
      apiPost<JobSummary>("/admin/index", mode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
      qc.invalidateQueries({ queryKey: ["admin", "manifest"] });
    },
  });
}

// ---- quality (golden / eval / drift / feedback) ----

export interface GoldenQuestion {
  question: string;
  expected?: string[];
  match_field?: string;
  filter?: Record<string, unknown>;
}

export interface EvalSummary {
  [metric: string]: number;
}

export interface DriftRegression {
  metric: string;
  baseline: number;
  current: number;
  delta: number;
}

export interface EvalResult {
  k: number;
  summary: EvalSummary;
  rows: Record<string, unknown>[];
  judge_rows?: JudgeRow[] | null; // only when the run had judge on (§B)
  baseline: EvalSummary | null;
  regressions: DriftRegression[];
}

// One LLM-judge row: full pipeline answer scored for faithfulness + relevancy.
export interface JudgeRow {
  question: string;
  faithfulness: number;
  relevancy: number;
  reason: string;
}

export interface FeedbackItem {
  i: number;
  ts?: string;
  principal?: string | null;
  tenant?: string | null; // pre-rename records
  question?: string;
  rating?: number;
  note?: string | null;
  chunk_ids?: string[];
}

export interface PagedItems<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export function useGolden() {
  return useQuery({
    queryKey: ["admin", "golden"],
    queryFn: () =>
      apiGet<{ path: string; questions: GoldenQuestion[] }>("/admin/golden"),
  });
}

export function useTriggerEval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ k, judge }: { k: number; judge: boolean }) =>
      apiPost<JobSummary>("/admin/eval", { k, judge }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "jobs"] }),
  });
}

export function useEvalBaseline() {
  return useQuery({
    queryKey: ["admin", "eval", "baseline"],
    queryFn: () => apiGet<{ baseline: EvalSummary | null }>("/admin/eval/baseline"),
  });
}

export interface EvalHistoryRun {
  ts?: string;
  k?: number;
  summary: EvalSummary;
  n_questions?: number;
  n_regressions?: number;
}

export function useEvalHistory(limit = 50) {
  return useQuery({
    queryKey: ["admin", "eval", "history", limit],
    queryFn: () =>
      apiGet<{ total: number; runs: EvalHistoryRun[] }>(
        `/admin/eval/history?limit=${limit}`,
      ),
  });
}

// ── self-validation (Loops 1 + 5) ───────────────────────────────────────────
// Differential check of the answer path against the stored numeric answer key.
// Cases are generated from the time-series store at run time — no golden file.

export interface SelfCheckParams {
  per_group?: number;
  max_cases?: number;
  per_kind?: number;
}

// One generated known-answer question. `status` localises a failure:
// statement_miss = generation bug, hydration_miss = structured-path bug.
export interface SelfCheckNumericRow {
  case_id: string;
  question: string;
  variable: string;
  scenario: string | null;
  period: string;
  source: string;
  expected: number;
  tol: number;
  hydrated: boolean;
  hydrated_value: number | null;
  stated: boolean;
  refused: boolean;
  status: "ok" | "statement_miss" | "hydration_miss" | "both_miss" | "refused";
  numbers_seen: number[];
}

// One negative control. `fabricated` is the only outcome counted against the
// system — `corrected` means it pushed back on a false premise.
export interface SelfCheckProbeRow {
  case_id: string;
  question: string;
  kind: "absent_scenario" | "absent_variable" | "false_premise";
  detail: string;
  outcome: "refused" | "corrected" | "fabricated" | "evasive";
  refused: boolean;
  named_real_scenarios: string[];
  numbers_seen: number[];
  answer_excerpt: string;
}

export interface SelfCheckResult {
  summary: EvalSummary;
  numeric: { summary: EvalSummary; rows: SelfCheckNumericRow[] };
  probes: { summary: EvalSummary; rows: SelfCheckProbeRow[] };
}

// Metrics where a LOWER value is better (mirrors eval/selfcheck.py LOWER_BETTER).
export const SELFCHECK_LOWER_BETTER = new Set([
  "fabrication_rate",
  "both_miss_rate",
  "evasive_rate",
]);

export function useTriggerSelfCheck() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: SelfCheckParams) =>
      apiPost<JobSummary>("/admin/selfcheck", params),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "jobs"] }),
  });
}

// ── realized-episode backtest (Loop 4) ──────────────────────────────────────
// Scores shock vectors against what markets actually did. The two modes are
// kept apart on purpose: grounding_fidelity measures translation when the
// evidence is visible, ex_ante_skill bounds retrieval to what existed at the
// time. A high ex-ante abstention rate reflects corpus depth, not quality.

export const BACKTEST_MODES = ["grounding_fidelity", "ex_ante_skill"] as const;
export type BacktestMode = (typeof BACKTEST_MODES)[number];

export const BACKTEST_MODE_LABEL: Record<BacktestMode, string> = {
  grounding_fidelity: "Grounding fidelity",
  ex_ante_skill: "Ex-ante skill",
};

export interface ShockReferenceField {
  value: number;
  unit: string;
  tol: number;
}

export interface BacktestEpisode {
  key: string;
  aka: string;
  scenario: string;
  start: string;
  end: string;
  pit_cutoff: string;
  fields: string[];
  reference: Record<string, ShockReferenceField>;
}

export interface BacktestFieldScore {
  reference: number;
  unit: string;
  emitted: number | null;
  present: boolean;
  unit_match: boolean | null;
  sign_match: boolean | null;
  abs_error: number | null;
  within_tol: boolean;
  tol: number | null;
}

export interface BacktestRow {
  episode: string;
  aka: string;
  mode: BacktestMode;
  scenario: string;
  pit_cutoff: string | null;
  abstained: boolean;
  summary: EvalSummary;
  fields: Record<string, BacktestFieldScore>;
}

export interface BacktestResult {
  summary: EvalSummary; // keys are mode-prefixed
  modes: Record<string, { summary: EvalSummary; rows: BacktestRow[] }>;
  episodes: BacktestEpisode[];
}

export function useBacktestEpisodes() {
  return useQuery({
    queryKey: ["admin", "backtest", "episodes"],
    queryFn: () =>
      apiGet<{ total: number; episodes: BacktestEpisode[] }>(
        "/admin/backtest/episodes",
      ),
  });
}

export function useTriggerBacktest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: { episodes?: string[]; modes?: BacktestMode[] }) =>
      apiPost<JobSummary>("/admin/backtest", params),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "jobs"] }),
  });
}

// Per-question eval rows, as returned in a completed eval job's `result.rows`.
export interface EvalRow {
  question: string;
  gold: string[];
  top: string[];
  [metric: string]: unknown; // hit@k / recall@k / mrr
}

export function useSetBaseline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (summary: EvalSummary) =>
      apiPost<{ status: string; baseline: EvalSummary }>("/admin/eval/baseline", {
        summary,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "eval", "baseline"] }),
  });
}

// §B structured-output health: per task/model ok/repaired/failed since process start.
export interface SchemaCounts {
  ok: number;
  repaired: number;
  failed: number;
  total: number;
  repair_rate: number;
}

export interface SchemaHealth {
  by_task: Record<string, Record<string, SchemaCounts>>;
  totals: SchemaCounts;
}

export function useSchemaHealth() {
  return useQuery({
    queryKey: ["admin", "schema-health"],
    queryFn: () => apiGet<SchemaHealth>("/admin/schema-health"),
    refetchInterval: 30_000,
  });
}

// §B feedback -> golden: positive, chunk-citing feedback proposed as candidate
// golden entries (written to a review file; never promoted automatically).
export interface GoldenCandidate {
  question: string;
  match_field: string;
  expected: string[];
  source: string;
  ts?: string | null;
}

export interface ReconcileResult {
  n_proposed: number;
  candidates: GoldenCandidate[];
  written_to: string;
}

export function useReconcileFeedback() {
  return useMutation({
    mutationFn: () => apiPost<ReconcileResult>("/admin/feedback/reconcile", {}),
  });
}

export function useFeedback(limit = 100, offset = 0) {
  return useQuery({
    queryKey: ["admin", "feedback", limit, offset],
    queryFn: () =>
      apiGet<PagedItems<FeedbackItem>>(`/admin/feedback?limit=${limit}&offset=${offset}`),
    refetchInterval: 30_000,
  });
}

// ---- ingestion ----

export interface TierSummary {
  name: string;
  tier?: number;
  title?: string;
  publisher?: string;
  n_endpoints?: number;
  n_enabled?: number;
  error?: string;
}

export interface EndpointConfig {
  id: string;
  handler: string;
  title: string;
  enabled: boolean;
  bucket: string;
  poll_cadence: string;
  discovery: { type: string; url: string };
  release: string | null;
  factor_tags: string[];
  entity_tags: string[];
}

export interface TierDetail {
  name: string;
  tier: number;
  title: string;
  publisher: string;
  licence: string;
  base_url: string;
  endpoints: EndpointConfig[];
}

export interface IngestRequest {
  tier: string;
  endpoints?: string[];
  limit?: number | null;
  refresh?: boolean;
}

export function useTiers() {
  return useQuery({
    queryKey: ["admin", "tiers"],
    queryFn: () => apiGet<{ tiers: TierSummary[] }>("/admin/tiers"),
  });
}

export function useTier(name: string | null) {
  return useQuery({
    queryKey: ["admin", "tier", name],
    queryFn: () => apiGet<TierDetail>(`/admin/tiers/${encodeURIComponent(name!)}`),
    enabled: !!name,
  });
}

export function useTriggerIngest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: IngestRequest) => apiPost<JobSummary>("/admin/ingest", req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "jobs"] }),
  });
}

// ---- curation ----

export interface Vocabulary {
  factor_tags: string[];
  entity_tags: string[];
  doc_types: string[];
  aliases: Record<string, string>;
}

export interface CurateRequest {
  task: string;
  dry_run?: boolean;
  strip_only?: boolean;
}

export function useVocabulary() {
  return useQuery({
    queryKey: ["admin", "vocabulary"],
    queryFn: () => apiGet<Vocabulary>("/admin/vocabulary"),
  });
}

export function useTriggerCurate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CurateRequest) => apiPost<JobSummary>("/admin/curate", req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "jobs"] }),
  });
}
