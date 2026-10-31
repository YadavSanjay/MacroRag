"""RSS 2.0 discovery-feed parsing (shared by every RSS-backed Fed endpoint).

The Fed serves ``press_monetary.xml``, ``sloos.xml`` and
``speeches_and_testimony.xml`` on an identical RSS 2.0 schema (Ingestion Design
§4.1): each ``<item>`` carries ``title``, ``link``, ``guid``, ``description``,
``category`` and an RFC-822 ``pubDate``. The title is the routing key.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timezone
from typing import Optional

import feedparser

from .config import DocTypeRule
from .logging_utils import get_logger
from .models import DiscoveredItem

log = get_logger(__name__)


def parse_pubdate(raw: str | None, parsed_struct=None) -> datetime | None:
    """Best-effort RFC-822 -> aware UTC datetime."""
    if parsed_struct:
        try:
            return datetime(*parsed_struct[:6], tzinfo=UTC)
        except (TypeError, ValueError):
            pass
    if not raw:
        return None
    from dateutil import parser as dtparser  # local import; optional dep

    try:
        dt = dtparser.parse(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, OverflowError):
        return None


def classify(title: str, rules: Iterable[DocTypeRule]) -> tuple[str, str, int]:
    """Return ``(doc_type, chunking, priority)`` for the first matching rule.

    Falls back to ``("unknown", "whole", 3)`` when no rule matches.
    """
    for rule in rules:
        if re.search(rule.match, title, flags=re.IGNORECASE):
            return rule.type, rule.chunking, rule.priority
    return "unknown", "whole", 3


def parse_rss(
    content: bytes,
    source_id: str,
    rules: Iterable[DocTypeRule],
) -> list[DiscoveredItem]:
    """Parse RSS bytes into classified :class:`DiscoveredItem` records."""
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        log.warning("[%s] RSS parse produced no entries (bozo=%s)",
                    source_id, getattr(feed, "bozo_exception", ""))
    rules = list(rules)
    items: list[DiscoveredItem] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        doc_type, chunking, priority = classify(title, rules)
        items.append(
            DiscoveredItem(
                source_id=source_id,
                title=title,
                url=link,
                doc_type=doc_type,
                chunking=chunking,
                priority=priority,
                published=parse_pubdate(entry.get("published"),
                                        entry.get("published_parsed")),
                is_pdf=link.lower().endswith(".pdf"),
            )
        )
    log.info("[%s] discovered %d feed items", source_id, len(items))
    return items
