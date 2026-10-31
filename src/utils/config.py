"""Configuration for the Macro RAG retrieval/generation layer.

Defaults live in code; an optional ``configs/rag.yaml`` overrides them, and the
environment supplies secrets (API keys via ``.env``, already loaded by the
ingest config). Everything is typed so the pipeline fails fast on a bad setting.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

try:  # reuse .env already loaded by the ingest layer if present
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None   # type: ignore[assignment]  # optional dependency

# Repo root = three levels up: src/utils/config.py
REPO_ROOT = Path(__file__).resolve().parents[2]
# MACRORAG_DOTENV=0 skips the repo-root dotenv file (the process env is used as
# given) -- for tests and their subprocesses, or a container that injects secrets.
if load_dotenv is not None and os.environ.get("MACRORAG_DOTENV", "1") != "0":
    load_dotenv(REPO_ROOT / ".env")


class EmbeddingSettings(BaseModel):
    model: str = "BAAI/bge-m3"
    dense_dim: int = 1024
    device: str | None = None        # None -> auto (cuda if available)
    batch_size: int = 16
    max_length: int = 8192
    # grounding prefix prepended before embedding (publisher | publication | date)
    use_grounding_prefix: bool = True


class QdrantSettings(BaseModel):
    # Local on-disk mode by default (no server); set ``url`` to use a server, or
    # ``location=":memory:"`` for ephemeral/test stores. Production should point
    # ``url`` at a Qdrant server (local on-disk mode deadlocks on bulk upsert).
    path: str | None = "data/vectorstore/qdrant"
    url: str | None = None
    location: str | None = None
    collection: str = "macro_chunks"
    dense_vector: str = "dense"
    sparse_vector: str = "sparse"
    quantization: bool = False       # int8 scalar quant: ~4x memory cut, ~1% recall hit
    # Build the sparse inverted index in RAM (not mmap'd on disk). The sparse
    # index for a corpus this size is tiny, and Qdrant's on-disk sparse index has
    # hit `OffsetOutOfBound` read panics (1.18.x) on certain weighted queries;
    # in-memory sidesteps that. Dense vectors stay on disk regardless.
    sparse_on_disk: bool = False
    # Managed / secured server-mode auth (server mode only — ignored on-disk). The
    # API key is read as a secret from this env var (supports the ``<NAME>_FILE``
    # mount convention); ``https``/``timeout``/``prefer_grpc`` are passed through to
    # the client only when set, so a plain in-VPC HTTP server needs none of them.
    api_key_env: str | None = None
    https: bool | None = None
    # TLS certificate verification. None leaves the client default (verify on).
    # Set false for a server with a self-signed cert on a private network — e.g.
    # the in-VPC Qdrant host, whose cert is self-signed; verification would fail
    # otherwise. Never set false against a publicly-reachable server.
    verify: bool | None = None
    timeout: float | None = None
    prefer_grpc: bool = False


class RerankerSettings(BaseModel):
    enabled: bool = True
    model: str = "BAAI/bge-reranker-v2-m3"
    device: str | None = None
    batch_size: int = 16


class InferenceSettings(BaseModel):
    """Shared **RagInference** service (one bge-m3 embedder + bge-reranker copy,
    shared with ARag) so MacroRAG doesn't load its own multi-GB models — the
    reason the service was factored out for constrained-RAM/CPU desktops.

    ``remote_url`` is the PROJECT DEFAULT: a locally-running service on :8799.
    The ``RAGINFER_URL`` env var overrides it (a full base URL to point elsewhere,
    or an empty value to force in-process models). ``fallback_local`` loads the
    models in-process when the service is unreachable, so a stopped service
    degrades gracefully instead of failing the query (env override:
    ``RAGINFER_FALLBACK_LOCAL``)."""
    remote_url: str = "http://localhost:8799"
    fallback_local: bool = True


class TierOptions(BaseModel):
    """Per-tier call options inside a provider profile (spec llm-output-budget).

    Unset fields keep the global behaviour (``llm.max_tokens``, the provider's default
    effort, ``llm.resilience.request_timeout_s``). Not applied yet: this block is the
    config surface the llm-output-budget change wires in.
    """
    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = Field(default=None, gt=0)    # output cap, thinking included
    effort: str | None = None        # Anthropic output_config.effort / OpenAI reasoning_effort
    timeout_s: float | None = Field(default=None, gt=0)   # per-call HTTP timeout


class ModelProfile(BaseModel):
    """Per-provider model tiers. Lets ``provider: anthropic`` remap all three
    routing tiers (and the fallback model) without re-pinning each one — see
    LLMSettings._apply_provider_profile. Keep model ids priced in
    configs/llm_pricing.yaml so cost accounting stays complete."""
    plan: str       # router/paraphrase/decompose/plan + verify (capable, cheap)
    answer: str      # generation for simple queries (cheapest tier)
    complex: str     # generation for complex routes + synthesis + factor discovery
    default: str | None = None       # client default / fallback; falls back to `answer`
    escalation: str | None = None    # optional tier ABOVE complex, for quality-signal
    #                                     escalation (design doc §5). None = no higher tier
    #                                     (so synthesis/complex-route escalation is a no-op).
    # Per-tier call options; a key outside the five tiers raises.
    tier_options: dict[Literal["plan", "answer", "complex", "default", "escalation"],
                       TierOptions] = Field(default_factory=dict)


# Code-default profiles for both providers. rag.yaml may override the whole
# `profiles` block, or pin an individual tier via the flat plan_model/answer_model/
# complex_model fields (which still win — see the validator). Anthropic ids below
# are placeholders confirmed against the claude-api skill + llm_pricing.yaml; tune
# in rag.yaml. See docs/design/Macro_RAG_LLM_Harness.md §6.
_DEFAULT_PROFILES: dict[str, dict[str, str]] = {
    "openai": {"plan": "gpt-5.4-mini", "answer": "gpt-5.4-nano",
               "complex": "gpt-5.4", "default": "gpt-5.4-nano"},
    "anthropic": {"plan": "claude-sonnet-4-6", "answer": "claude-haiku-4-5",
                  "complex": "claude-opus-4-8", "default": "claude-haiku-4-5"},
}

# Replacement model set, selected by `llm.use_replacement_models` (off by default) or
# the MACRORAG_USE_REPLACEMENT_MODELS env var. OpenAI shuts gpt-5.4-nano down on
# 2027-04-01 and names gpt-6-luna as its replacement; gpt-6-sol takes the plan and
# complex tiers. Anthropic moves to the current Claude 5.5 models. A provider absent
# here keeps its current profile.
_REPLACEMENT_PROFILES: dict[str, dict[str, str]] = {
    "openai": {"plan": "gpt-6-sol", "answer": "gpt-6-luna",
               "complex": "gpt-6-sol", "default": "gpt-6-luna"},
    "anthropic": {"plan": "claude-sonnet-5-5", "answer": "claude-haiku-5-5",
                  "complex": "claude-opus-5-5", "default": "claude-haiku-5-5"},
}

_REPLACEMENT_ENV = "MACRORAG_USE_REPLACEMENT_MODELS"
_TRUE_WORDS = ("1", "true", "yes", "on")
_FALSE_WORDS = ("0", "false", "no", "off")

_PROVIDER_KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


def default_profiles(use_replacement: bool) -> dict[str, dict[str, str]]:
    """Code-default model tiers per provider, for the current or the replacement set.

    Args:
        use_replacement: True selects the replacement OpenAI models.

    Returns:
        A fresh `{provider: {tier: model}}` map with the plan, answer, complex and
        default tiers.
    """
    profiles = {p: dict(t) for p, t in _DEFAULT_PROFILES.items()}
    if use_replacement:
        profiles.update({p: dict(t) for p, t in _REPLACEMENT_PROFILES.items()})
    return profiles


def replacement_models_enabled(env: Mapping[str, str] | None = None,
                               default: bool = False) -> bool:
    """Whether the replacement model set is selected.

    Args:
        env: Environment to read `MACRORAG_USE_REPLACEMENT_MODELS` from; `os.environ`
            when None.
        default: The configured value, used when the variable is absent.

    Returns:
        The variable's boolean value when present, else `default`.

    Raises:
        ValueError: The variable holds a value that is not a recognised boolean.
    """
    source = os.environ if env is None else env
    raw = source.get(_REPLACEMENT_ENV)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_WORDS:
        return True
    if value in _FALSE_WORDS:
        return False
    raise ValueError(f"{_REPLACEMENT_ENV}={raw!r} is not a boolean "
                     f"(use one of {', '.join(_TRUE_WORDS + _FALSE_WORDS)})")


class ResilienceSettings(BaseModel):
    """Bounded retry + circuit breaker for provider LLM calls (Production Hardening §H).

    A transient 429/5xx/timeout is retried with exponential backoff + full jitter;
    after ``breaker_threshold`` consecutive failed calls the breaker opens for
    ``breaker_reset_s`` to fail fast instead of hammering a degraded provider.
    See src/llm/resilience.py.
    """
    request_timeout_s: float = 60.0   # per-call HTTP timeout handed to the SDK client
    max_retries: int = 3              # retries AFTER the first attempt (0 = no retry)
    backoff_base_s: float = 0.5       # first backoff ≈ [0, base); doubles each retry
    backoff_max_s: float = 8.0        # per-retry backoff cap (before jitter)
    breaker_threshold: int = 5        # consecutive failed calls before the breaker opens
    breaker_reset_s: float = 30.0     # cool-down before a half-open trial


class LLMSettings(BaseModel):
    provider: Literal["openai", "anthropic"] = "openai"
    model: str = "gpt-5.4-nano"      # default / fallback model (resolved from profile)
    temperature: float = 0.1
    # Completion cap per call. Must fit a fully-cited answer's JSON — at 1500 a
    # DECOMPOSE answer citing several chunks gets truncated mid-JSON, fails to
    # parse, and the prose answer comes back empty. Plan/verify/synthesize emit
    # only a few hundred tokens, so the higher cap only bites on generation.
    max_tokens: int = 4000
    api_key_env: str = "OPENAI_API_KEY"  # resolved from provider unless set explicitly
    # Tiered routing (ARag review §2.2.2): cheap models for plan/verify, a
    # frontier model only when the router flags a complex query.
    # plan_model also drives the PLAN_EXECUTE planner; a too-weak model (e.g.
    # gpt-5.4-nano) plans poorly and degrades retrieval. gpt-5.4-mini is capable
    # enough (it invokes structured lookups) and far cheaper than gpt-5.4.
    # These flat fields, when set explicitly (config or code), OVERRIDE the active
    # provider's profile; when left at their defaults they are filled from the
    # profile so switching provider remaps them. See _apply_provider_profile.
    plan_model: str = "gpt-5.4-mini"     # router/paraphrase/decompose/plan + verify
    answer_model: str = "gpt-5.4-nano"   # generation for simple queries
    complex_model: str = "gpt-5.4"       # generation for complex queries
    escalation_model: str | None = None   # optional tier above complex (see profile.escalation)
    complex_routes: list[str] = Field(
        default_factory=lambda: ["DECOMPOSE", "DECOMPOSE_AND_PARAPHRASE", "PLAN_EXECUTE"])
    prompt_cache: bool = True            # Anthropic system-prefix cache breakpoint
    # Provider-aware model tiers. Defaults cover openai+anthropic; a rag.yaml
    # `profiles:` block replaces this map wholesale.
    profiles: dict[str, ModelProfile] = Field(
        default_factory=lambda: {p: ModelProfile.model_validate(t)
                                 for p, t in _DEFAULT_PROFILES.items()})
    # Feature switch: take each provider's tiers from `replacement_profiles` instead
    # of `profiles`. MACRORAG_USE_REPLACEMENT_MODELS overrides it per process.
    use_replacement_models: bool = False
    replacement_profiles: dict[str, ModelProfile] = Field(
        default_factory=lambda: {p: ModelProfile.model_validate(t)
                                 for p, t in _REPLACEMENT_PROFILES.items()})
    resilience: ResilienceSettings = Field(default_factory=ResilienceSettings)

    @model_validator(mode="after")
    def _apply_provider_profile(self) -> LLMSettings:
        """Fill model tiers + api key env from the active provider's profile,
        UNLESS the corresponding flat field was set explicitly (which wins). This
        makes `provider: anthropic` remap the tiers without re-pinning each one."""
        fs = self.model_fields_set
        prof = None
        if replacement_models_enabled(default=self.use_replacement_models):
            prof = self.replacement_profiles.get(self.provider)
        if prof is None:
            prof = self.profiles.get(self.provider)
        if prof is not None:
            if "plan_model" not in fs:
                self.plan_model = prof.plan
            if "answer_model" not in fs:
                self.answer_model = prof.answer
            if "complex_model" not in fs:
                self.complex_model = prof.complex
            if "escalation_model" not in fs:
                self.escalation_model = prof.escalation
            if "model" not in fs:
                self.model = prof.default or prof.answer
        if "api_key_env" not in fs:
            self.api_key_env = _PROVIDER_KEY_ENV.get(self.provider, self.api_key_env)
        return self


class RedisSettings(BaseModel):
    """Single source for the Redis endpoint used by the cross-replica shared-state
    backends (Production Hardening §I): L1/L2 cache, rate-limit buckets, job lock + job store.
    Each feature has its own ``backend: memory|redis`` toggle and falls back to
    in-process if Redis is unreachable, so a single-box deploy needs no Redis."""
    url: str = "redis://localhost:6379/0"


class CacheSettings(BaseModel):
    enabled: bool = True
    ttl_seconds: int = 3600
    max_entries: int = 2048
    backend: Literal["memory", "redis"] = "memory"
    # Deprecated: prefer the top-level ``redis.url``. Kept for back-compat — a
    # non-default value here still wins for the L1 cache (see build_cache).
    redis_url: str = "redis://localhost:6379/0"


class SemanticCacheSettings(BaseModel):
    # L2: reuse a prior answer when the query embedding is near-identical.
    enabled: bool = False            # needs an embedder; on for the served stack
    threshold: float = 0.97          # cosine similarity to count as a hit
    max_entries: int = 1024
    # memory = per-replica numpy store; redis = cross-replica shared vector cache
    # (§I). Redis path falls back to memory if the client/library is unavailable.
    backend: Literal["memory", "redis"] = "memory"


class RankingSettings(BaseModel):
    # Bucket-aware time decay so "latest supersedes" (bucket B) ranks newer
    # releases higher. Applied as a multiplicative boost on the rerank score.
    time_decay: bool = False
    half_life_days: float = 180.0
    # per-bucket recency sensitivity (A=catalyst, B=periodic, C=rolling)
    bucket_decay: dict[str, float] = Field(
        default_factory=lambda: {"A": 1.0, "B": 1.0, "C": 0.5})


class AuthSettings(BaseModel):
    enabled: bool = False
    # api_key -> **principal**: the caller's identity for auth, rate limiting and
    # cost attribution. It is NOT a data-isolation boundary — the corpus is shared,
    # and no ingest path writes a per-caller partition key. See the dormant
    # ``MetaFilter.tenant_id`` hook for what isolation would need.
    api_keys: dict[str, str] = Field(default_factory=dict)
    rate_limit_rpm: int = 60         # per-key requests/minute
    # memory = per-replica token bucket (effective limit = rpm × replicas — a bug
    # under horizontal scale); redis = a shared fixed-window counter so the cap is
    # global across replicas (§I). Falls back to memory if Redis is unreachable.
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    # Keys allowed to call the /admin/* maintenance + system surface. Must also be
    # present in ``api_keys``. When auth is disabled the admin surface is open too.
    admin_api_keys: list[str] = Field(default_factory=list)


class JobSettings(BaseModel):
    """Admin maintenance-job coordination (§I). ``backend: redis`` makes the
    exclusive-group lock and the job-record store cross-replica + restart-durable;
    ``memory`` keeps today's in-process behaviour."""
    backend: Literal["memory", "redis"] = "memory"
    # Lock TTL must exceed the longest exclusive job (a full re-embed is ~80 min) so
    # the lock isn't lost mid-build; it also auto-expires if a holder replica dies.
    lock_ttl_s: int = 7200
    record_ttl_s: int = 604_800      # persisted job records live 7 days
    idempotency_ttl_s: int = 3600    # a repeated idempotency key dedups for 1 h


class ApiSettings(BaseModel):
    """HTTP-surface hardening (Production Hardening §L).

    All off/unbounded by default so a single-box dev run is unchanged; tighten for a
    public deployment.
    """
    # CORS allowed origins. Empty = no CORS headers (same-origin only). Use explicit
    # origins in prod; "*" is allowed but disables credentialed CORS by spec.
    cors_allow_origins: list[str] = Field(default_factory=list)
    # Admission control on the CPU-rerank bottleneck: max concurrent in-flight
    # pipeline requests. 0 = unlimited (today's behaviour); >0 returns HTTP 503 when
    # the in-flight count is exceeded, rather than queueing unboundedly under load.
    max_concurrency: int = 0
    max_concurrency_wait_s: float = 0.0   # secs to wait for a slot before 503 (0 = fail fast)


class ObservabilitySettings(BaseModel):
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"   # OTLP gRPC collector
    service_name: str = "macro-rag"
    # Prometheus /metrics endpoint + real-request-latency middleware (§J). Harmless
    # and cheap (in-process counters), so on by default; scrape it from Prometheus.
    metrics_enabled: bool = True


class AlertingSettings(BaseModel):
    """Outbound alerts for governance signals (Production Hardening §K).

    Covers eval regressions and cost-cap breaches. Posts a Slack-compatible JSON payload
    (``{"text": ...}``) to ``webhook_url`` (also works for PagerDuty/Teams-style incoming
    webhooks). Off by default; when on but no webhook is set, alerts are logged at
    WARNING (so the signal is never silently dropped).
    """
    enabled: bool = False
    webhook_url: str | None = None
    timeout_s: float = 5.0


class EvalSettings(BaseModel):
    """Enforcement policy for the drift/regression gate (Production Hardening §K)."""
    alert_on_regression: bool = True    # fire an alert when the eval job regresses vs baseline
    fail_on_regression: bool = False    # also mark the eval job FAILED on regression (opt-in)
    tolerance: float = 0.05             # metric drop below baseline that counts as a regression
    judge: bool = False                 # also run the LLM-judge answer-quality pass (§B):
    #                                     folds faithfulness/relevancy into the eval summary


class CostSettings(BaseModel):
    """Hard spend ceilings on the base pipeline (Production Hardening §K).

    Goes beyond the escalation-only ``cost_budget_usd``. Daily counters are
    per-principal and global, keyed by date; a request is admitted only if neither
    ceiling is already hit, and its measured cost is recorded on completion. ``0``
    disables a given ceiling. ``backend: redis`` shares the counters across replicas (§I).
    """
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    backend: Literal["memory", "redis"] = "memory"
    per_request_usd: float = 0.0        # alert (not block) when one request exceeds this
    # Block one caller once its day's spend hits this. ``per_tenant_daily_usd`` is
    # accepted as a deprecated alias so existing config files keep loading.
    per_principal_daily_usd: float = Field(
        default=0.0, validation_alias=AliasChoices("per_principal_daily_usd",
                                                   "per_tenant_daily_usd"))
    daily_usd: float = 0.0              # block everyone once the day's global spend hits this


class RetrievalSettings(BaseModel):
    dense_limit: int = 40            # per-branch dense prefetch
    sparse_limit: int = 40           # per-branch sparse prefetch
    fusion_limit: int = 40           # post-RRF candidates per branch
    rerank_candidates: int = 60      # pooled candidates fed to the reranker
    final_k: int = 8                 # chunks handed to the generator
    use_hyde: bool = True
    use_decompose: bool = True
    use_router: bool = True
    mmr_lambda: float = 1.0          # 1.0 = pure relevance (MMR off), <1 diversifies
    chunk_expansion: bool = True     # pull prev/next neighbours of winners
    hydrate_timeseries: bool = True  # attach matching numeric series to the answer
    plan_execute: bool = True        # typed plan + calculator for quantitative queries
    # P0 hardening ------------------------------------------------------------
    min_rerank_score: float = 0.0    # relevance floor; below it -> refuse (0 = off).
                                     # bge-reranker scores are 0-1; ~0.3 is sensible.
    max_branch_workers: int = 4      # thread-pool fan-out across retrieval branches
    verify_faithfulness: bool = True # post-gen check of each claim vs its citations
    # Iterative multi-hop retrieval: walk a causal chain, each hop's evidence
    # seeding the next sub-question. 0/1 = off (single-shot). >1 enables up to N
    # hops — each is an extra LLM call + rerank pass, so it's CPU-costly.
    multi_hop_max_hops: int = 0


class SynthesisSettings(BaseModel):
    # Citation gate: min dated chunk_ids a shock field must cite to survive.
    min_citation_chunks: int = 1
    # Allow analog/proxy grounding (P3). When False, analog-grounded fields are
    # dropped after the gate so only directly-measured shocks are emitted.
    enable_analog: bool = True
    # Numeric-validation / coherence pass (synthesis/validate.py): flag magnitudes
    # outside a horizon-scaled plausibility band, moves that dwarf a hydrated
    # series' observed span, and cross-field sign incoherence. Surface-only by
    # default (down-weight, don't discard). (Named to avoid shadowing pydantic's
    # BaseModel.validate.)
    numeric_validation: bool = True
    # Reference horizon (days) the plausibility bands are calibrated at; the caps
    # scale ~sqrt(horizon/reference) for longer scenarios.
    validation_reference_horizon_days: int = 90
    # When True, drop fields carrying a hard magnitude failure instead of only
    # flagging them (coherence/anchor flags are always surface-only).
    drop_invalid: bool = False
    # Source-horizon check (spec synth-analog-horizon, runs inside the numeric
    # validation pass): flag a field whose evidence describes a move over a horizon
    # more than `horizon_tolerance` times away from the requested one
    # (`horizon_mismatch`, hard), or whose horizon is unknown (`horizon_unknown`).
    horizon_check: bool = True
    horizon_tolerance: float = 3.0
    # Sign-agreement check: flag a field whose sign contradicts the caller's
    # requested direction for it. Not used yet; read by the coming sign-agreement
    # change.
    direction_check: bool = True
    # Upgrade a field's grounding_mode analog->direct when its own scenario series
    # was in the hydrated DATA and the emitted sign agrees with that path (the LLM
    # sometimes mislabels a scenario's own path as an analog).
    reconcile_grounding: bool = True
    # Winner slots reserved for quantified scenario studies (SCENARIO_STUDY_TAGS) so
    # the magnitude anchors reach the model rather than just being eligible. 0 = off.
    scenario_study_slots: int = 2
    # Rerank pool cap for synthesize only (reserved studies count inside it). CPU
    # rerank costs ~8s/doc on the shared service; None = the retriever's own cap.
    rerank_candidates: int | None = 12


class EscalationSettings(BaseModel):
    """Quality-signal-driven model escalation (design doc §5). When a cheap-model
    pass deteriorates — the faithfulness verifier drops many claims, or the citation
    gate loses many shock fields — retry the same generation on a higher model tier
    (LLMSettings.escalation_model, else complex_model) and keep the result only if it
    is measurably better. Every upgrade is recorded in the query trace's
    ``escalations`` array + a ``:escalated`` call in the usage log.

    Off by default (opt-in, like multi_hop / semantic_cache). With the default
    profiles, only the simple-route prose path (answer_model -> complex_model) has a
    higher tier; set an ``escalation`` tier in the provider profile to also enable
    complex-route / synthesis escalation."""
    enabled: bool = False
    max_per_request: int = 1          # hard cap on escalated re-runs per query
    cost_budget_usd: float = 0.05     # abort escalation once this request's LLM spend exceeds this
    verifier_drop_ratio: float = 0.34 # > this fraction of cited claims dropped -> escalate prose
    gate_loss_ratio: float = 0.34     # > this fraction of emitted shock fields gated -> escalate synth


class TraceSettings(BaseModel):
    enabled: bool = True
    path: str = "data/traces/queries.jsonl"
    # Capture each LLM call's raw prompt+response into the per-query call log
    # (surfaced in the Trace Explorer). Token/cost accounting is always recorded;
    # this only gates the (potentially large) prompt+response text.
    capture_llm: bool = True
    max_prompt_chars: int = 20_000   # per-field truncation cap for captured text


class ObjectStoreSettings(BaseModel):
    """Where the corpus **object data** (raw bytes, processed ``ParsedDocument``
    JSON, and the index manifest) lives. ``backend: local`` keeps today's behavior
    — everything under the local ``data/`` tree; ``backend: s3`` routes those three
    artifacts to an S3 bucket (dual-mode deploy, see
    docs/design/Macro_RAG_AWS_Deployment.md).

    Only object-shaped corpus data moves to S3. The SQLite time-series + ingest
    caches and the JSONL traces/eval artifacts stay on the local/EBS filesystem
    (single-writer, append-shaped) and are unaffected by this setting.

    ``backend`` defaults to follow ``deploy_mode`` (``s3`` when ``aws``) unless set
    explicitly. The S3 layout mirrors the local ``data/`` tree under ``s3_prefix``:
    ``{prefix}/raw/ingest/…``, ``{prefix}/processed/ingest/…``,
    ``{prefix}/vectorstore/manifest.json``. Credentials come from the ambient AWS
    chain (EC2 instance profile / env), never this config."""
    backend: Literal["local", "s3"] | None = None   # None -> derive from deploy_mode
    s3_bucket: str | None = None
    s3_prefix: str = "corpus"
    s3_region: str | None = None


class Paths(BaseModel):
    processed_dir: str = "data/processed/ingest"
    timeseries_db: str = "data/vectorstore/timeseries.sqlite"
    manifest: str = "data/vectorstore/manifest.json"
    feedback_log: str = "data/traces/feedback.jsonl"
    golden: str = "configs/eval/golden.yaml"
    # Review file for feedback→golden candidates (§B) — NOT auto-promoted into golden.
    golden_candidates: str = "data/eval/golden_candidates.yaml"
    eval_baseline: str = "data/eval/baseline.json"
    eval_history: str = "data/eval/history.jsonl"   # append-only log of past eval runs

    def resolve(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else REPO_ROOT / p


class RagSettings(BaseModel):
    # Deployment target. ``local`` = everything on the local filesystem (default,
    # unchanged). ``aws`` = corpus object data on S3 (see ObjectStoreSettings);
    # SQLite/traces stay on the local/EBS filesystem. Env: MACRORAG_DEPLOY_MODE.
    deploy_mode: Literal["local", "aws"] = "local"
    storage: ObjectStoreSettings = Field(default_factory=ObjectStoreSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    inference: InferenceSettings = Field(default_factory=InferenceSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    synthesis: SynthesisSettings = Field(default_factory=SynthesisSettings)
    escalation: EscalationSettings = Field(default_factory=EscalationSettings)
    trace: TraceSettings = Field(default_factory=TraceSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    semantic_cache: SemanticCacheSettings = Field(default_factory=SemanticCacheSettings)
    ranking: RankingSettings = Field(default_factory=RankingSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    jobs: JobSettings = Field(default_factory=JobSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    alerting: AlertingSettings = Field(default_factory=AlertingSettings)
    eval: EvalSettings = Field(default_factory=EvalSettings)
    cost: CostSettings = Field(default_factory=CostSettings)
    paths: Paths = Field(default_factory=Paths)

    # ── convenience path resolvers ───────────────────────────────────────────
    @property
    def processed_path(self) -> Path:
        return self.paths.resolve(self.paths.processed_dir)

    @property
    def timeseries_path(self) -> Path:
        return self.paths.resolve(self.paths.timeseries_db)

    @property
    def trace_path(self) -> Path:
        return self.paths.resolve(self.trace.path)

    @property
    def manifest_path(self) -> Path:
        return self.paths.resolve(self.paths.manifest)

    @property
    def feedback_path(self) -> Path:
        return self.paths.resolve(self.paths.feedback_log)

    @property
    def golden_path(self) -> Path:
        return self.paths.resolve(self.paths.golden)

    @property
    def golden_candidates_path(self) -> Path:
        return self.paths.resolve(self.paths.golden_candidates)

    @property
    def eval_baseline_path(self) -> Path:
        return self.paths.resolve(self.paths.eval_baseline)

    @property
    def eval_history_path(self) -> Path:
        return self.paths.resolve(self.paths.eval_history)

    def qdrant_path(self) -> Path | None:
        if self.qdrant.path and not (self.qdrant.url or self.qdrant.location):
            return self.paths.resolve(self.qdrant.path)
        return None

    @property
    def storage_backend(self) -> str:
        """Effective object-store backend: explicit ``storage.backend`` wins, else
        derived from ``deploy_mode`` (``s3`` when ``aws``, else ``local``)."""
        if self.storage.backend is not None:
            return self.storage.backend
        return "s3" if self.deploy_mode == "aws" else "local"

    def secret(self, env_name: str) -> str | None:
        return read_secret(env_name)


def read_secret(env_name: str) -> str | None:
    """Resolve a secret (§L). Prefer a ``<NAME>_FILE`` env pointing at a file — the
    Docker/K8s *secret mount* convention, so keys need not sit in the process env or
    a committed ``.env`` — else fall back to the ``<NAME>`` env var directly. Returns
    None if neither is set (or the file can't be read)."""
    file_path = os.environ.get(f"{env_name}_FILE")
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return os.environ.get(env_name)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _env_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay deployment env vars onto the YAML config dict so a container/Helm
    deployment can point the service at its own Qdrant/OTel endpoints without
    baking a ``rag.yaml`` into the image. Precedence: env > rag.yaml > code
    defaults. Only endpoints (non-secret wiring) live here; API-key *secrets* are
    still read at use-time via ``RagSettings.secret`` (env). Mutates and returns
    ``cfg``. See docs/design/Macro_RAG_Production_Hardening.md §G.

    Recognised vars:
      MACRORAG_DEPLOY_MODE     -> deploy_mode (local|aws)
      MACRORAG_STORAGE_BACKEND -> storage.backend (local|s3; else derived from mode)
      MACRORAG_S3_BUCKET       -> storage.s3_bucket
      MACRORAG_S3_PREFIX       -> storage.s3_prefix
      MACRORAG_S3_REGION       -> storage.s3_region
      MACRORAG_QDRANT_URL     -> qdrant.url (point at a Qdrant server)
      MACRORAG_REDIS_URL      -> redis.url (shared-state backend, §I)
      MACRORAG_OTEL_ENDPOINT  -> observability.otel_endpoint (+ implies enable
                                 unless rag.yaml explicitly set otel_enabled)
      MACRORAG_OTEL_ENABLED   -> observability.otel_enabled (explicit bool override)
    """
    deploy_mode = os.environ.get("MACRORAG_DEPLOY_MODE")
    if deploy_mode:
        cfg["deploy_mode"] = deploy_mode.strip().lower()

    for env_name, key in (("MACRORAG_STORAGE_BACKEND", "backend"),
                          ("MACRORAG_S3_BUCKET", "s3_bucket"),
                          ("MACRORAG_S3_PREFIX", "s3_prefix"),
                          ("MACRORAG_S3_REGION", "s3_region")):
        val = os.environ.get(env_name)
        if val:
            cfg.setdefault("storage", {})[key] = val

    qurl = os.environ.get("MACRORAG_QDRANT_URL")
    if qurl:
        cfg.setdefault("qdrant", {})["url"] = qurl

    redis_url = os.environ.get("MACRORAG_REDIS_URL")
    if redis_url:
        cfg.setdefault("redis", {})["url"] = redis_url

    otel_endpoint = os.environ.get("MACRORAG_OTEL_ENDPOINT")
    otel_enabled = os.environ.get("MACRORAG_OTEL_ENABLED")
    if otel_endpoint or otel_enabled is not None:
        obs = cfg.setdefault("observability", {})
        if otel_endpoint:
            obs["otel_endpoint"] = otel_endpoint
            # Injecting an endpoint implies the operator wants OTel on, unless
            # rag.yaml explicitly pinned otel_enabled (respect the explicit value).
            obs.setdefault("otel_enabled", True)
        if otel_enabled is not None:  # explicit env wins over the implicit enable
            obs["otel_enabled"] = _env_truthy(otel_enabled)
    return cfg


@lru_cache(maxsize=1)
def load_settings() -> RagSettings:
    """Load RAG settings from ``configs/rag.yaml`` (optional) over code defaults,
    then overlay deployment env overrides (Qdrant/OTel endpoints — see
    ``_apply_env_overrides``). Raises ``ValueError`` if a configured model is
    unpriced (`load_settings_from`)."""
    return load_settings_from(REPO_ROOT / "configs" / "rag.yaml")


def tier_options_for(llm: LLMSettings, tier: str | None) -> TierOptions:
    """The active profile's options for one model tier (spec llm-output-budget C1).

    Args:
        llm: The LLM settings.
        tier: ``plan``, ``answer``, ``complex``, ``default``, ``escalation``, or None.

    Returns:
        The tier's options, or ``TierOptions()`` when none apply.
    """
    if tier is None:
        return TierOptions()
    prof = None
    # The same profile the model tiers came from (_apply_provider_profile).
    if replacement_models_enabled(default=llm.use_replacement_models):
        prof = llm.replacement_profiles.get(llm.provider)
    if prof is None:
        prof = llm.profiles.get(llm.provider)
    if prof is None:
        return TierOptions()
    return prof.tier_options.get(tier) or TierOptions()  # type: ignore[call-overload]  # tier is a free str; an unknown name misses


# Reasoning-effort values each provider's API takes (spec llm-output-budget Notes):
# Anthropic output_config.effort; OpenAI reasoning_effort (gpt-5.4 none..xhigh,
# gpt-6 adds max). Whether a model takes a given level is checked by the live eval.
_EFFORTS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "openai": frozenset({"none", "low", "medium", "high", "xhigh", "max"}),
}


def _check_efforts(llm: LLMSettings) -> None:
    """Raises ValueError naming every tier option whose effort its provider rejects."""
    bad = [f"{group}.{provider}.tier_options.{tier}.effort={opts.effort!r}"
           for group, profiles in (("profiles", llm.profiles),
                                   ("replacement_profiles", llm.replacement_profiles))
           for provider, prof in profiles.items()
           for tier, opts in prof.tier_options.items()
           if opts.effort is not None
           and opts.effort not in _EFFORTS.get(provider, frozenset())]
    if bad:
        raise ValueError(f"llm: effort not accepted by its provider: {bad} "
                         f"(allowed: { {p: sorted(e) for p, e in _EFFORTS.items()} })")


def configured_models(llm: LLMSettings) -> list[str]:
    """Every model id the LLM settings can reach (spec llm-model-registry C2).

    Args:
        llm: The LLM settings.

    Returns:
        The sorted, de-duplicated ids of every tier of every provider's profile and replacement
        profile, plus the resolved tier fields.
    """
    ids: set[str] = set()
    for prof in (*llm.profiles.values(), *llm.replacement_profiles.values()):
        ids.update(m for m in (prof.plan, prof.answer, prof.complex, prof.default,
                               prof.escalation) if m)
    ids.update(m for m in (llm.model, llm.plan_model, llm.answer_model, llm.complex_model,
                           llm.escalation_model) if m)
    return sorted(ids)


def load_settings_from(path: Path) -> RagSettings:
    """Loads settings from a YAML file, as `load_settings` loads ``configs/rag.yaml``.

    Specs llm-model-registry C3 and llm-output-budget C2.

    Args:
        path: The YAML file.

    Returns:
        The settings, with deployment env overrides applied.

    Raises:
        ValueError: A configured model has no rate-card row, or a tier option sets an
            effort its provider does not take.
    """
    # Deferred: the llm package imports this module.
    from llm.pricing import require_priced

    settings = RagSettings(**_apply_env_overrides(_read_yaml(path)))
    require_priced(configured_models(settings.llm))
    _check_efforts(settings.llm)
    return settings


@lru_cache(maxsize=1)
def _vocab_aliases() -> dict[str, str]:
    data = _read_yaml(REPO_ROOT / "configs" / "ingest" / "vocabulary.yaml")
    return dict(data.get("aliases", {}))


def normalize_api_tags(tags: list[str]) -> list[str]:
    """Lenient normalization of caller-supplied facet tags (e.g. AITrade's
    scenario-synthesis filters): lowercase + alias map (hyperscalers->hyperscaler,
    taiwan->china, tsmc/nvidia->semiconductors, …), no warnings; unknown tokens
    are preserved (harmless under MatchAny/OR filtering)."""
    aliases = _vocab_aliases()
    out: list[str] = []
    for t in tags or []:
        low = str(t).lower()
        canon = aliases.get(t, aliases.get(low, low))
        if canon not in out:
            out.append(canon)
    return out
