"""Configuration loading for the ingestion module.

Two layers:

* :class:`CommonSettings` — global HTTP / storage / chunking settings loaded
  from ``configs/ingest/common.yaml`` (overridable by environment for paths).
* :class:`TierConfig` — a tier endpoint catalog loaded from e.g.
  ``configs/ingest/tier1_fed.yaml``.

API keys/secrets are NOT stored in YAML — they live in ``.env`` and are read
via :func:`get_secret` (loaded once with python-dotenv).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

from .logging_utils import get_logger

log = get_logger(__name__)

# Repo root = three levels up from this file: src/ingest/common/config.py
REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "configs" / "ingest"

# Load .env once at import so get_secret() works everywhere; MACRORAG_DOTENV=0
# skips it (see utils.config).
if os.environ.get("MACRORAG_DOTENV", "1") != "0":
    load_dotenv(REPO_ROOT / ".env")


# ── Global / common settings ─────────────────────────────────────────────────


class HttpSettings(BaseModel):
    user_agent: str = "MacroRAG/1.0"
    rate_limit_per_sec: float = 1.0
    timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_factor: float = 1.5
    retry_statuses: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])


class StorageSettings(BaseModel):
    data_dir: str = "data"
    raw_subdir: str = "raw/ingest"
    processed_subdir: str = "processed/ingest"
    cache_db: str = "cache/ingest_cache.sqlite"

    def _root(self) -> Path:
        data = Path(self.data_dir)
        if not data.is_absolute():
            data = REPO_ROOT / data
        return data

    @property
    def raw_dir(self) -> Path:
        return self._root() / self.raw_subdir

    @property
    def processed_dir(self) -> Path:
        return self._root() / self.processed_subdir

    @property
    def cache_path(self) -> Path:
        return self._root() / self.cache_db


class ChunkingSettings(BaseModel):
    max_tokens: int = 1000
    overlap_tokens: int = 80
    chars_per_token: int = 4


class CommonSettings(BaseModel):
    http: HttpSettings = Field(default_factory=HttpSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    provenance: dict[str, Any] = Field(default_factory=dict)


# ── Tier endpoint catalog ────────────────────────────────────────────────────


class DiscoveryConfig(BaseModel):
    # rss / html_index = scrape-driven; api = REST endpoint the handler queries
    # (e.g. Federal Register); predictable_url = construct-and-probe (e.g. TIC).
    type: Literal["rss", "html_index", "api", "predictable_url"]
    url: str
    selector: str | None = None


class DocTypeRule(BaseModel):
    match: str                           # regex tested against the item title/anchor
    type: str
    chunking: str = "whole"
    priority: int = 1


class ReleaseSchedule(BaseModel):
    frequency: str
    months: list[str] = Field(default_factory=list)
    note: str | None = None


class EndpointConfig(BaseModel):
    id: str
    handler: str
    title: str
    enabled: bool = True
    publisher: str | None = None   # overrides tier publisher (e.g. BEA vs BLS)
    licence: str | None = None     # overrides tier licence (e.g. JPM EOTM public)
    bucket: str = "C"
    poll_cadence: str = "daily"
    discovery: DiscoveryConfig
    landing_url: str | None = None
    release_schedule: ReleaseSchedule | None = None
    factor_tags: list[str] = Field(default_factory=list)
    entity_tags: list[str] = Field(default_factory=list)
    trades: list[str] = Field(default_factory=list)
    doc_type_rules: list[DocTypeRule] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _canonicalize_tags(self) -> EndpointConfig:
        """Normalize factor/entity tags against the controlled vocabulary at load
        time: legacy/variant terms self-heal via the alias map, and any tag not
        in the registry is logged so config drift is caught early."""
        self.factor_tags = normalize_tags(self.factor_tags, "factor_tags", context=self.id)
        self.entity_tags = normalize_tags(self.entity_tags, "entity_tags", context=self.id)
        return self


class TierConfig(BaseModel):
    tier: int
    name: str
    publisher: str
    licence: str = "public_domain_us_gov"
    base_url: str
    defaults: dict[str, Any] = Field(default_factory=dict)
    endpoints: list[EndpointConfig]

    def endpoint(self, endpoint_id: str) -> EndpointConfig:
        for ep in self.endpoints:
            if ep.id == endpoint_id:
                return ep
        raise KeyError(f"No endpoint '{endpoint_id}' in tier {self.tier} config")


# ── Loaders ──────────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# Module-level so tests can redirect it (and so save + load agree on one path).
VOCAB_PATH = CONFIG_DIR / "vocabulary.yaml"


@lru_cache(maxsize=1)
def load_vocabulary() -> dict[str, Any]:
    """Load and cache the controlled vocabulary registry (vocabulary.yaml)."""
    data = _read_yaml(VOCAB_PATH)
    return {
        "factor_tags": set(data.get("factor_tags", [])),
        "entity_tags": set(data.get("entity_tags", [])),
        "aliases": dict(data.get("aliases", {})),
        "doc_types": {dt for grp in (data.get("doc_types") or {}).values() for dt in grp},
    }


def save_vocabulary_fields(*, factor_tags: list[str] | None = None,
                           entity_tags: list[str] | None = None,
                           aliases: dict[str, str] | None = None) -> dict[str, Any]:
    """Curation edit (§E): update the provided top-level vocabulary fields in
    ``VOCAB_PATH`` (others — e.g. the grouped ``doc_types`` — preserved), invalidate
    the cache, and return the reloaded vocabulary. ``None`` fields are left as-is."""
    data = _read_yaml(VOCAB_PATH)
    if factor_tags is not None:
        data["factor_tags"] = sorted({str(t) for t in factor_tags})
    if entity_tags is not None:
        data["entity_tags"] = sorted({str(t) for t in entity_tags})
    if aliases is not None:
        data["aliases"] = {str(k): str(v) for k, v in aliases.items()}
    VOCAB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with VOCAB_PATH.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    load_vocabulary.cache_clear()
    return load_vocabulary()


def normalize_tags(tags: list[str], kind: str, *, context: str = "") -> list[str]:
    """Canonicalize a tag list against the registry: apply the alias map, dedupe
    (order-preserving), and warn on any term outside the closed set. ``kind`` is
    ``"factor_tags"`` or ``"entity_tags"``. Shared by config load + migration."""
    vocab = load_vocabulary()
    aliases, allowed = vocab["aliases"], vocab[kind]
    out: list[str] = []
    for t in tags:
        canon = aliases.get(t, t)
        if allowed and canon not in allowed:
            log.warning("vocab: %s '%s'%s not in registry — add it to "
                        "vocabulary.yaml or fix the source",
                        kind, canon, f" [{context}]" if context else "")
        if canon not in out:
            out.append(canon)
    return out


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    """Expand ``${VAR}`` / ``${VAR:-default}`` in every string of a loaded YAML tree.

    Keeps operator-specific values (e.g. the User-Agent contact address) in ``.env``
    instead of the committed config. An empty variable counts as unset, so it takes
    the default.

    Args:
        value: A string, or a dict/list of them as returned by ``yaml.safe_load``;
            other scalars pass through unchanged.

    Returns:
        The same structure with every reference replaced.

    Raises:
        KeyError: A ``${VAR}`` without a default names an unset variable.
    """
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    if not isinstance(value, str):
        return value

    def _sub(m: re.Match[str]) -> str:
        name, default = m.group(1), m.group(2)
        found = os.environ.get(name)
        if found:
            return found
        if default is None:
            raise KeyError(f"config references ${{{name}}}, which is not set (add it to .env)")
        return default

    return _ENV_REF.sub(_sub, value)


@lru_cache(maxsize=1)
def load_common_settings() -> CommonSettings:
    """Load and cache the global common.yaml settings (``${VAR:-default}`` expanded)."""
    return CommonSettings(**expand_env(_read_yaml(CONFIG_DIR / "common.yaml")))


def load_tier_config(name: str) -> TierConfig:
    """Load a tier catalog by file stem, e.g. ``load_tier_config("tier1_fed")``."""
    stem = name[:-5] if name.endswith(".yaml") else name
    return TierConfig(**_read_yaml(CONFIG_DIR / f"{stem}.yaml"))


def get_secret(key: str, default: str | None = None) -> str | None:
    """Read an API key/secret. Prefer a ``<KEY>_FILE`` env pointing at a mounted
    file (the Docker/K8s / AWS Secrets Manager convention), else the ``<KEY>`` env
    var (``.env`` already loaded). Mirrors ``utils.config.read_secret`` so ingest
    source keys resolve the same way as the LLM keys."""
    file_path = os.environ.get(f"{key}_FILE")
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return os.environ.get(key, default)
