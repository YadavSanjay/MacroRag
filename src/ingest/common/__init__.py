"""Common, tier-agnostic ingestion building blocks.

Everything reusable across tiers lives here: config loading, the polite HTTP
client, conditional-GET cache, raw/document stores, RSS + HTML + PDF parsing,
section-aware chunking, and the two-stage ingestor scaffold. Tier packages
(e.g. ``ingest.fed``) compose these rather than re-implementing them.
"""

from __future__ import annotations

from . import feeds, html_extract, pdf_extract
from .base import (
    EndpointHandler,
    IngestContext,
    RunReport,
    TierIngestor,
)
from .cache import FetchCache
from .chunking import Chunker
from .config import (
    CommonSettings,
    EndpointConfig,
    TierConfig,
    get_secret,
    load_common_settings,
    load_tier_config,
)
from .handler_base import BaseSourceHandler
from .http_client import PoliteClient
from .logging_utils import get_logger, setup_logging
from .models import (
    Chunk,
    ChunkMetadata,
    DiscoveredItem,
    FetchResult,
    ParsedDocument,
)
from .storage import DocumentStore, RawStore

__all__ = [
    "feeds", "html_extract", "pdf_extract",
    "EndpointHandler", "IngestContext", "RunReport", "TierIngestor",
    "BaseSourceHandler", "FetchCache", "Chunker",
    "CommonSettings", "EndpointConfig", "TierConfig",
    "get_secret", "load_common_settings", "load_tier_config",
    "PoliteClient", "get_logger", "setup_logging",
    "Chunk", "ChunkMetadata", "DiscoveredItem", "FetchResult", "ParsedDocument",
    "DocumentStore", "RawStore",
    "build_context",
]


def build_context(tier_config_name: str, *, refresh: bool = False) -> IngestContext:
    """Wire up an :class:`IngestContext` from the common + tier YAML configs.

    ``refresh=True`` makes Stage-A discovery bypass the conditional-GET cache
    (re-list everything; content idempotency still dedups). The caller owns the
    lifecycle: close ``ctx.client`` / ``ctx.cache`` when done (the CLI does this).
    """
    settings = load_common_settings()
    tier = load_tier_config(tier_config_name)
    cache = FetchCache(settings.storage.cache_path)

    # Per-tier overrides from `defaults:` (rate limit + User-Agent). Tier 5 needs
    # a browser-like UA — some house sites 403 a bot UA (e.g. goldmansachs.com).
    http = settings.http.model_copy()
    tier_rate = tier.defaults.get("rate_limit_per_sec")
    if tier_rate:
        http.rate_limit_per_sec = float(tier_rate)
    tier_ua = tier.defaults.get("user_agent")
    if tier_ua:
        http.user_agent = str(tier_ua)

    client = PoliteClient(http, cache=cache)
    # Carry the effective (possibly UA-overridden) http settings on the context
    # so the headless-browser fetch uses the same UA as the HTTP client.
    eff_settings = settings.model_copy(update={"http": http})
    return IngestContext(
        settings=eff_settings,
        tier=tier,
        client=client,
        cache=cache,
        raw_store=RawStore(settings.storage),
        doc_store=DocumentStore(settings.storage),
        chunker=Chunker(settings.chunking),
        refresh=refresh,
    )
