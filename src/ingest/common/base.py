"""Two-stage ingestion scaffold shared by every tier (Ingestion Design §4.0).

* :class:`IngestContext` bundles the shared resources (HTTP client, cache,
  stores, chunker, settings) so handlers stay thin.
* :class:`EndpointHandler` is the per-source contract:
  ``discover()`` (Stage A) -> ``DiscoveredItem``s; ``fetch()`` (Stage B) ->
  ``ParsedDocument``s.
* :class:`TierIngestor` drives the generic loop: discover, skip already-seen
  documents via the cache ledger, fetch+parse new ones, persist, and report a
  :class:`RunReport`.

Concrete tiers (e.g. ``fed``) supply a :class:`TierConfig` and a handler
registry; they do not re-implement the loop, the caching, or the persistence.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .cache import FetchCache
from .chunking import Chunker
from .config import CommonSettings, EndpointConfig, TierConfig
from .http_client import PoliteClient
from .logging_utils import get_logger
from .models import DiscoveredItem, ParsedDocument
from .storage import DocumentStore, RawStore

log = get_logger(__name__)


@dataclass
class IngestContext:
    """Shared resources handed to every endpoint handler."""

    settings: CommonSettings
    tier: TierConfig
    client: PoliteClient
    cache: FetchCache
    raw_store: RawStore
    doc_store: DocumentStore
    chunker: Chunker
    today: date = field(default_factory=date.today)
    # When True, Stage-A discovery bypasses the conditional-GET cache so a
    # "fetch latest available" run re-lists every item (content idempotency via
    # the seen-documents ledger still skips already-parsed docs). Set by --refresh.
    refresh: bool = False


@dataclass
class RunReport:
    """Outcome summary for a single ingestion run."""

    tier: int
    discovered: int = 0
    new: int = 0
    parsed: int = 0
    skipped: int = 0
    errors: int = 0
    documents: list[str] = field(default_factory=list)

    def merge(self, other: RunReport) -> None:
        self.discovered += other.discovered
        self.new += other.new
        self.parsed += other.parsed
        self.skipped += other.skipped
        self.errors += other.errors
        self.documents.extend(other.documents)

    def __str__(self) -> str:
        return (
            f"tier {self.tier}: discovered={self.discovered} new={self.new} "
            f"parsed={self.parsed} skipped={self.skipped} errors={self.errors}"
        )


class EndpointHandler(abc.ABC):
    """Per-source Stage A / Stage B contract."""

    def __init__(self, cfg: EndpointConfig, ctx: IngestContext):
        self.cfg = cfg
        self.ctx = ctx

    @abc.abstractmethod
    def discover(self) -> list[DiscoveredItem]:
        """Stage A: poll discovery URL, return candidate documents."""

    @abc.abstractmethod
    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        """Stage B: fetch + parse a single discovered item into document(s)."""


class TierIngestor:
    """Generic driver around a tier's handler registry."""

    #: handler-id -> EndpointHandler subclass, supplied by the concrete tier
    handler_registry: dict[str, type[EndpointHandler]] = {}

    def __init__(self, ctx: IngestContext):
        self.ctx = ctx

    def _handler_for(self, cfg: EndpointConfig) -> EndpointHandler | None:
        cls = self.handler_registry.get(cfg.handler)
        if cls is None:
            log.error("no handler registered for '%s' (endpoint %s)",
                      cfg.handler, cfg.id)
            return None
        return cls(cfg, self.ctx)

    def run(self, endpoint_ids: Iterable[str] | None = None,
            limit_per_endpoint: int | None = None) -> RunReport:
        report = RunReport(tier=self.ctx.tier.tier)
        wanted = set(endpoint_ids) if endpoint_ids else None
        for cfg in self.ctx.tier.endpoints:
            if not cfg.enabled:
                continue
            if wanted and cfg.id not in wanted:
                continue
            handler = self._handler_for(cfg)
            if handler is None:
                report.errors += 1
                continue
            report.merge(self._run_endpoint(handler, cfg, limit_per_endpoint))
        log.info("RUN COMPLETE — %s", report)
        return report

    def _run_endpoint(self, handler: EndpointHandler, cfg: EndpointConfig,
                      limit: int | None) -> RunReport:
        rep = RunReport(tier=self.ctx.tier.tier)
        log.info("[%s] Stage A — discovery (%s)", cfg.id, cfg.discovery.url)
        try:
            items = handler.discover()
        except Exception as exc:
            log.exception("[%s] discovery failed: %s", cfg.id, exc)
            rep.errors += 1
            return rep

        rep.discovered = len(items)
        items.sort(key=lambda it: it.priority)  # high priority first
        processed = 0
        for item in items:
            if limit is not None and processed >= limit:
                break
            try:
                docs = handler.fetch(item)
            except Exception as exc:
                log.exception("[%s] fetch failed for %s: %s", cfg.id, item.url, exc)
                rep.errors += 1
                continue
            if not docs:
                rep.skipped += 1
                continue
            for doc in docs:
                self.ctx.doc_store.save(doc)
                self.ctx.cache.mark_processed(doc.url, cfg.id, doc.content_hash)
                rep.parsed += 1
                rep.new += 1
                rep.documents.append(doc.doc_id)
            processed += 1
        log.info("[%s] %s", cfg.id, rep)
        return rep
