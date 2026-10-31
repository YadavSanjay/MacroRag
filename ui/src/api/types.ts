// Hand-written types mirroring the FastAPI Pydantic models in src/api/models.py
// and the dict serializers in src/pipelines/query_pipeline.py.
//
// Once the backend is running, `npm run gen:api` regenerates a typed client from
// the live OpenAPI schema into src/api/schema.gen.ts; these stay as the stable
// shapes the UI codes against.

export type QueryMode = "answer" | "synthesize";

export interface FilterModel {
  tiers: number[];
  buckets: string[];
  doc_types: string[];
  entity_tags: string[];
  factor_tags: string[];
  publishers: string[];
  date_from?: string | null;
  date_to?: string | null;
}

export function emptyFilter(): FilterModel {
  return {
    tiers: [],
    buckets: [],
    doc_types: [],
    entity_tags: [],
    factor_tags: [],
    publishers: [],
    date_from: null,
    date_to: null,
  };
}

// Structured controls for mode="synthesize" (mirrors SynthesisOptions in
// src/api/models.py). All optional — when omitted the synthesizer infers
// everything from the free-text question.
export interface SynthesisOptions {
  fields?: string[]; // restrict emitted shock vector to these field names (empty = all)
  horizon_days?: number | null; // pin the scenario horizon structurally
  scenario?: string | null; // extra scenario narrative folded into retrieval + synthesis
  // expected sign per field ("up" = positive under positive_means); a field with the
  // other sign is flagged direction_mismatch (hard)
  directions?: Record<string, ShockDirection>;
}

export type ShockDirection = "up" | "down";

export interface QueryRequest {
  question: string;
  filters?: FilterModel | null;
  mode?: QueryMode;
  synthesis?: SynthesisOptions | null; // synthesize mode only
}

export type RetrievalRoute =
  | "DIRECT"
  | "PARAPHRASE"
  | "DECOMPOSE"
  | "HYDE"
  | "PLAN_EXECUTE";

export interface AnswerDetail {
  text: string;
  chunk_ids: string[];
}

export interface TimeseriesHit {
  source: string;
  publisher: string;
  n_rows: number;
  note?: string | null;
}

export interface ChunkBrief {
  chunk_id: string;
  source_id: string;
  publisher: string;
  publication: string;
  release_date: string;
  doc_type: string;
  rerank_score: number | null; // null for expanded neighbour chunks (no rerank)
  expanded: boolean;
}

export interface ShockField {
  value: number;
  unit: string;
  chunk_ids: string[];
  rationale?: string | null;
  grounding_mode?: "direct" | "analog"; // analog = proxied on a historical episode / adverse path
  validation?: string[]; // validation/coherence codes (see lib/shockVocab HARD_VALIDATION_CODES)
  source_horizon_days?: number; // horizon the cited evidence describes, as declared
}

// Per-synthesize diagnostics (mirrors SynthesisDiagnostics.as_dict() in
// src/synthesis/diagnostics.py) — WHY each shock field did / did not ground.
export interface SynthesisDrop {
  field: string;
  stage: string;
  reason: string;
  [k: string]: unknown;
}

export interface SynthesisDiagnostics {
  outcome: string; // grounded | partial | all_dropped | llm_empty | upstream_refused | no_chunks
  coverage_gap: boolean; // true when no grounded shock fields were produced
  skipped: string | null;
  n_chunks: number;
  n_dated_chunks: number;
  emitted_fields: string[]; // LLM-emitted (pre-validation)
  kept_fields: string[]; // survived synth + citation gate
  analog_fields: string[]; // kept via analog/proxy grounding
  dropped: SynthesisDrop[];
  validation: Record<string, unknown>[];
  // requested (synthesis.fields) but not in the vector, in request order; empty
  // when no fields were requested. Absent on responses from older servers.
  missing_fields?: MissingField[];
}

export interface MissingField {
  field: string;
  reason: "dropped" | "not_emitted";
  codes: string[]; // the drop's codes when reason = dropped
  card_field: boolean; // whether any market-episode card can measure the field
}

export interface QueryResultDto {
  summary: string;
  refused: boolean;
  details: AnswerDetail[];
  unanswered: string[];
  unsupported: string[];
  used_chunk_ids: string[];
  timeseries: TimeseriesHit[];
  route: RetrievalRoute;
  cached: boolean;
  chunks: ChunkBrief[];
  // synthesize mode only:
  shock_vector?: Record<string, ShockField>;
  world_view_id?: string | null;
  synthesis_diagnostics?: SynthesisDiagnostics;
}

// ---- factor discovery (POST /factors) ----

export type FactorRelevance = "high" | "medium" | "low";

export interface RelevantFactor {
  factor_tag: string;
  relevance: FactorRelevance;
  entity_tags: string[];
  chunk_ids: string[];
  rationale?: string;
}

export interface FactorRequest {
  portfolio: string;
  filters?: FilterModel | null;
  top_k?: number;
}

export interface FactorsResultDto {
  factors: RelevantFactor[];
  route: RetrievalRoute;
  chunks: ChunkBrief[];
  world_view_id?: string | null;
}

// ---- shock-field schema (GET /synthesis/schema) ----

export interface ShockSchemaField {
  name: string;
  units: string[];
  preferred_unit: string;
  positive_means: string;
}

export interface SynthesisSchemaDto {
  fields: ShockSchemaField[];
  grounding_modes: Record<string, string>;
}

export interface CitationDto extends ChunkBrief {
  text: string;
  url?: string | null;
  section?: string | null;
}

export interface FeedbackRequest {
  question: string;
  rating: 1 | -1;
  note?: string | null;
  chunk_ids?: string[];
}

export interface HealthDto {
  status: string;
  built: boolean;
  index: Record<string, unknown>;
}

// ---- streaming (POST /query/stream, SSE) ----

export type StreamEvent =
  | { type: "status"; stage: string }
  | { type: "retrieved"; n_chunks: number; route: RetrievalRoute; cached: boolean }
  | { type: "answer"; result: QueryResultDto }
  | { type: "done" };
