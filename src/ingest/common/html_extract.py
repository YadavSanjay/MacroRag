"""Fed HTML body extraction (Ingestion Design §4.6).

~95% of a federalreserve.gov page is shared navigation chrome; the article body
is a small block. Naive html->text swamps the corpus with menu noise, so we:

1. try DOM-targeted extraction via the configured CSS selectors
   (``#article`` on release pages, the bootstrap column on publication pages);
2. fall back to the breadcrumb -> ``Last Update:`` content boundaries;
3. fall back to trafilatura's main-content heuristic.

Also exposes :func:`find_links` for scraping HTML index pages (FSR, DFAST) for
their linked PDFs.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .logging_utils import get_logger

log = get_logger(__name__)

_LAST_UPDATE = re.compile(r"Last\s+Update[:\s]", re.IGNORECASE)


# Block-level tags after which a paragraph break belongs. A non-whitespace
# sentinel is appended (it survives get_text(strip=True)) then mapped to "\n\n",
# so the budget chunker has real paragraph boundaries to split on instead of
# seeing the whole article body as one giant unbroken unit.
_BLOCK_TAGS = ["p", "div", "section", "article", "li", "tr", "blockquote",
               "h1", "h2", "h3", "h4", "h5", "h6", "br"]
_PARA_SENTINEL = chr(0xE000)   # private-use marker: non-whitespace, survives strip


def _clean_text(node) -> str:
    for tag in node.find_all(["script", "style", "nav", "noscript"]):
        tag.decompose()
    for tag in node.find_all(_BLOCK_TAGS):
        tag.append(_PARA_SENTINEL)
    text = node.get_text(" ", strip=True).replace(_PARA_SENTINEL, "\n\n")
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)   # tidy whitespace around breaks
    # collapse runs of blank lines
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_body(
    html: bytes | str,
    selectors: Iterable[str] | None = None,
) -> str:
    """Extract the meaningful article body from a Fed HTML page."""
    soup = BeautifulSoup(html, "lxml")

    # 1) DOM-targeted selectors
    for sel in selectors or ["#article"]:
        node = soup.select_one(sel)
        if node:
            text = _clean_text(node)
            if len(text) > 200:
                return _trim_to_last_update(text)

    # 2) breadcrumb -> "Last Update:" boundary on the full text
    full = _clean_text(soup.body or soup)
    trimmed = _trim_to_last_update(full)
    if len(trimmed) > 200:
        return trimmed

    # 3) trafilatura fallback
    try:
        import trafilatura

        extracted = trafilatura.extract(str(soup)) or ""
        if len(extracted) > 200:
            return extracted.strip()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("trafilatura fallback failed: %s", exc)

    return full


def _trim_to_last_update(text: str) -> str:
    """Cut everything from the ``Last Update:`` footer line onward."""
    m = _LAST_UPDATE.search(text)
    return text[: m.start()].strip() if m else text


def extract_article(html: bytes | str, selectors: Iterable[str] | None = None) -> str:
    """Trafilatura-first extraction for general article sites (Tier 5 houses).

    :func:`extract_body` is tuned for federalreserve.gov (CSS selectors +
    breadcrumb/``Last Update:`` boundaries) and falls back to full-page text on
    sites lacking those markers — which leaves nav chrome on commercial pages.
    For house-view article pages, trafilatura's main-content heuristic is far
    cleaner, so try it first and fall back to :func:`extract_body`.
    """
    try:
        import trafilatura

        text = html if isinstance(html, str) else html.decode("utf-8", "ignore")
        extracted = trafilatura.extract(text) or ""
        if len(extracted) > 400:
            return extracted.strip()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("extract_article trafilatura failed: %s", exc)
    return extract_body(html, selectors)


def extract_tables(html: bytes | str) -> list[list[list[str]]]:
    """Extract every HTML ``<table>`` as a row grid of cell text.

    Reusable for structured pages (e.g. the FOMC SEP projection tables), where a
    proper ``<th>``/``<td>`` grid is far cleaner to parse than the equivalent PDF
    with its stacked multi-row headers.
    """
    soup = BeautifulSoup(html, "lxml")
    grids: list[list[list[str]]] = []
    for table in soup.find_all("table"):
        grid: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells:
                grid.append(cells)
        if grid:
            grids.append(grid)
    return grids


def find_links(
    html: bytes | str,
    base_url: str,
    *,
    href_pattern: str | None = None,
    text_pattern: str | None = None,
) -> list[tuple[str, str]]:
    """Return ``(absolute_url, anchor_text)`` pairs from an index page.

    Filter by ``href_pattern`` (regex on the URL) and/or ``text_pattern``
    (regex on the anchor text). Used to scrape FSR/DFAST PDF links.
    """
    soup = BeautifulSoup(html, "lxml")
    href_re = re.compile(href_pattern, re.IGNORECASE) if href_pattern else None
    text_re = re.compile(text_pattern, re.IGNORECASE) if text_pattern else None

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        anchor = a.get_text(" ", strip=True)
        if href_re and not href_re.search(href):
            continue
        if text_re and not text_re.search(anchor):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append((absolute, anchor))
    return out
