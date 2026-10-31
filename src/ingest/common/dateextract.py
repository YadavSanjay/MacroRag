"""Best-effort publication-date extraction from fetched documents.

Some narrative sources (Tier 5 house views: JPM EOTM, BlackRock, GS Top of Mind,
MS Thoughts on the Market) publish at a URL with no machine-readable release date,
so their chunks would carry ``release_date=None`` and be silently un-citable by the
synthesis citation gate (``src/synthesis/gate.py``).

This derives the date *from the document* — HTML metadata / visible dateline, or a
PDF's dateline / embedded creation date. It is grounded in the source, not a guess,
and returns ``None`` when nothing confident is found (it never fabricates a date;
callers keep ``None`` and the field simply stays un-citable, as before).
"""

from __future__ import annotations

import io
import re
from datetime import date
from typing import Optional

from .logging_utils import get_logger

log = get_logger(__name__)

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

# "January 15, 2026" / "Jan. 15, 2026"
_MDY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
                  r"(\d{1,2}),?\s+(20\d{2})\b", re.IGNORECASE)
# "15 January 2026"
_DMY = re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                  r"[a-z]*\.?\s+(20\d{2})\b", re.IGNORECASE)
# "2026-01-15"
_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")

# Datelines sit near the top of a document; a body can mention many other dates.
_HEAD_CHARS = 4000


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    # a plausible publication window (guards OCR noise / page numbers)
    return dt if 2000 <= dt.year <= 2100 else None


def _first_date_in(text: str, max_date: date | None = None) -> date | None:
    """Earliest well-formed date in the document head (the dateline heuristic).

    ``max_date`` (typically the crawl date) drops candidates in the future: a
    publication date can't postdate ingestion, so a later match is a chart/axis or
    projection date, not the dateline.
    """
    if not text:
        return None
    head = text[:_HEAD_CHARS]
    cands: list[tuple[int, date]] = []
    for m in _MDY.finditer(head):
        d = _valid(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        if d:
            cands.append((m.start(), d))
    for m in _DMY.finditer(head):
        d = _valid(int(m.group(3)), _MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
        if d:
            cands.append((m.start(), d))
    for m in _ISO.finditer(head):
        d = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            cands.append((m.start(), d))
    if max_date is not None:
        cands = [(pos, d) for pos, d in cands if d <= max_date]
    if not cands:
        return None
    cands.sort(key=lambda t: t[0])
    return cands[0][1]


def _iso_prefix(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def extract_date_from_text(text: str, max_date: date | None = None) -> date | None:
    """Parse the first plausible dateline from free text (e.g. PDF body).

    ``max_date`` (crawl date) rejects future dates — see :func:`_first_date_in`.
    """
    return _first_date_in(text, max_date)


def extract_date_from_html(html: bytes | str,
                           max_date: date | None = None) -> date | None:
    """Publication date from HTML: structured metadata first, then a dateline."""
    def _ok(d: date | None) -> date | None:
        return d if d and (max_date is None or d <= max_date) else None

    # 1) trafilatura's metadata heuristic (handles most news/article sites)
    try:
        import trafilatura

        text = html if isinstance(html, str) else html.decode("utf-8", "ignore")
        md = trafilatura.extract_metadata(text)
        if md is not None and getattr(md, "date", None):
            d = _ok(_iso_prefix(md.date))
            if d:
                return d
    except Exception as exc:  # pragma: no cover - defensive (optional dep / API drift)
        log.debug("trafilatura metadata date failed: %s", exc)

    # 2) explicit publication meta tags + <time datetime>
    visible = ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for attr, val in (("property", "article:published_time"),
                          ("itemprop", "datePublished"),
                          ("name", "datePublished"), ("name", "date"),
                          ("name", "publishdate"), ("property", "og:published_time"),
                          ("name", "content_publishedAt")):  # morganstanley.com CMS
            tag = soup.find("meta", attrs={attr: val})
            if tag and tag.get("content"):
                d = _ok(_iso_prefix(tag["content"]))
                if d:
                    return d
        t = soup.find("time")
        if t and t.get("datetime"):
            d = _ok(_iso_prefix(t["datetime"]))
            if d:
                return d
        visible = soup.get_text(" ", strip=True)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("html meta date parse failed: %s", exc)
        visible = html if isinstance(html, str) else html.decode("utf-8", "ignore")

    # 3) visible dateline text
    return _first_date_in(visible, max_date)


def extract_date_from_pdf(content: bytes,
                          max_date: date | None = None) -> date | None:
    """Fallback: a born-digital PDF's embedded creation date."""
    try:
        from pypdf import PdfReader

        meta = PdfReader(io.BytesIO(content)).metadata
        raw = getattr(meta, "creation_date", None) if meta else None
        if raw is not None and hasattr(raw, "year"):
            d = _valid(raw.year, raw.month, raw.day)
            if d and (max_date is None or d <= max_date):
                return d
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("pdf metadata date failed: %s", exc)
    return None
