"""PDF text/table extraction for born-digital Fed/Treasury/IMF PDFs.

Scope note: the full ARag "page-image-first + DETR table detection + VLM table
summarization" pipeline (Source Catalog §9A) is a separate, heavier component.
This module provides the text/table layer the ingestion stage needs to produce
chunk-ready output, with a documented seam (:func:`extract_pages`) where the
page-image pipeline plugs in later.

Primary path: ``pypdf`` for fast text. ``pdfplumber`` is used opportunistically
for table extraction (FSR/DFAST/SEP tables -> the time-series store).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from .logging_utils import get_logger

log = get_logger(__name__)


def sanitize_text(text: str) -> str:
    """Drop lone UTF-16 surrogates that some PDFs emit (pypdf), which would
    otherwise raise UnicodeEncodeError at JSON serialization time."""
    if not text:
        return text
    return text.encode("utf-8", "ignore").decode("utf-8", "ignore")


@dataclass
class PdfPage:
    number: int
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)


def extract_pages(content: bytes, *, with_tables: bool = False) -> list[PdfPage]:
    """Extract per-page text (and optionally tables) from PDF bytes."""
    pages = _extract_text_pypdf(content)
    if with_tables:
        _augment_tables_pdfplumber(content, pages)
    return pages


def extract_text(content: bytes) -> str:
    """Full document text, page-break joined."""
    return "\n\n".join(p.text for p in _extract_text_pypdf(content) if p.text)


def _extract_text_pypdf(content: bytes) -> list[PdfPage]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pypdf is required for PDF ingestion") from exc

    reader = PdfReader(io.BytesIO(content))
    pages: list[PdfPage] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - malformed page
            log.warning("pypdf page %d extraction failed: %s", i, exc)
            text = ""
        pages.append(PdfPage(number=i, text=sanitize_text(text.strip())))
    log.debug("pypdf extracted %d pages", len(pages))
    return pages


def _augment_tables_pdfplumber(content: bytes, pages: list[PdfPage]) -> None:
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover
        log.debug("pdfplumber not available; skipping table extraction")
        return
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for i, page in enumerate(pdf.pages):
                if i >= len(pages):
                    break
                try:
                    tables = page.extract_tables() or []
                except Exception:  # pragma: no cover
                    tables = []
                pages[i].tables = [
                    [[(c or "").strip() for c in row] for row in tbl] for tbl in tables
                ]
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("pdfplumber table extraction failed: %s", exc)
