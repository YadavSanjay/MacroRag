"""Section-aware chunking for ingestion (Source Catalog §9A title-aware chunker).

Strategies (selected per doc via the config ``chunking`` field):

* ``whole``       — one chunk (FOMC statements, framework reaffirmations).
* ``sections``    — split on known section headings (minutes, SLOOS, FSR/DFAST
                    PDFs); each oversized section is further split by token budget.
* ``paragraphs``  — paragraph-cluster split for long speeches with no headings.
* ``pdf_sections``— alias of ``sections`` for PDF-sourced bodies.

A document-level chunker emits :class:`Chunk` objects already stamped with
:class:`ChunkMetadata`, so handlers just supply text + base metadata.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Optional

from .config import ChunkingSettings
from .models import Chunk, ChunkMetadata


def estimate_tokens(text: str, chars_per_token: int = 4) -> int:
    return max(1, len(text) // max(chars_per_token, 1))


_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def _hard_wrap(atom: str, max_chars: int) -> list[str]:
    """Last-resort fixed-window slice for an unbreakable run (no sentence/para
    boundary longer than the budget — e.g. a giant table row or URL list)."""
    return [atom[i:i + max_chars].strip() for i in range(0, len(atom), max_chars)]


def _atomize(text: str, max_chars: int) -> list[str]:
    """Break body into atoms each <= max_chars: paragraphs first, then (for an
    over-long paragraph) sentences, then a hard char-window fallback. This is
    what makes the splitter robust to bodies with no blank-line breaks (HTML
    speeches/podcasts) or single mega-paragraphs (rule text, dense PDF prose)."""
    atoms: list[str] = []
    for para in (p.strip() for p in re.split(r"\n{2,}", text) if p.strip()):
        if len(para) <= max_chars:
            atoms.append(para)
            continue
        buf = ""
        for sent in (s.strip() for s in _SENTENCE.split(para) if s.strip()):
            if len(sent) > max_chars:                      # unbreakable sentence
                if buf:
                    atoms.append(buf)
                    buf = ""
                atoms.extend(s for s in _hard_wrap(sent, max_chars) if s)
            elif buf and len(buf) + len(sent) + 1 > max_chars:
                atoms.append(buf)
                buf = sent
            else:
                buf = f"{buf} {sent}".strip() if buf else sent
        if buf:
            atoms.append(buf)
    return atoms


def _split_by_budget(text: str, max_tokens: int, overlap_tokens: int,
                     chars_per_token: int) -> list[str]:
    """Greedy pack into <= max_tokens windows with token overlap.

    Units are *atoms* guaranteed to fit the budget (see :func:`_atomize`), so no
    emitted window can blow past ``max_tokens`` by more than the overlap tail.
    """
    max_chars = max_tokens * chars_per_token
    overlap_chars = overlap_tokens * chars_per_token
    atoms = _atomize(text, max_chars)
    if not atoms:
        return [text] if text.strip() else []

    windows: list[str] = []
    buf = ""
    for atom in atoms:
        if buf and len(buf) + len(atom) + 2 > max_chars:
            windows.append(buf.strip())
            tail = buf[-overlap_chars:] if overlap_chars else ""
            buf = (tail + "\n\n" + atom).strip()
        else:
            buf = f"{buf}\n\n{atom}".strip() if buf else atom
    if buf.strip():
        windows.append(buf.strip())
    return windows


def _split_on_headings(text: str, headings: Iterable[str]) -> list[tuple[str | None, str]]:
    """Split body text at the configured section headings.

    Returns ``(section_title, section_text)`` pairs; the preamble before the
    first heading is returned with ``section_title=None``.
    """
    headings = [h for h in headings if h]
    if not headings:
        return [(None, text)]
    pattern = re.compile(
        r"(?im)^\s*(?:\d+\.\s*)?(" + "|".join(re.escape(h) for h in headings) + r")\s*$"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return [(None, text)]

    sections: list[tuple[str | None, str]] = []
    if matches[0].start() > 0:
        pre = text[: matches[0].start()].strip()
        if pre:
            sections.append((None, pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            sections.append((m.group(1).strip(), body))
    return sections


class Chunker:
    """Turns a parsed body + base metadata into grounded, sized chunks."""

    def __init__(self, settings: ChunkingSettings):
        self.settings = settings

    def chunk(
        self,
        text: str,
        *,
        doc_id: str,
        base_metadata: ChunkMetadata,
        strategy: str = "whole",
        section_headings: Iterable[str] | None = None,
    ) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []

        if strategy == "whole":
            units: list[tuple[str | None, str]] = [(None, text)]
        elif strategy in ("sections", "pdf_sections"):
            units = _split_on_headings(text, section_headings or [])
        elif strategy == "paragraphs":
            units = [(None, text)]
        else:  # unknown strategy -> safe default
            units = [(None, text)]

        chunks: list[Chunk] = []
        seq = 0
        for section_title, body in units:
            pieces = (
                [body]
                if estimate_tokens(body, self.settings.chars_per_token) <= self.settings.max_tokens
                else _split_by_budget(
                    body,
                    self.settings.max_tokens,
                    self.settings.overlap_tokens,
                    self.settings.chars_per_token,
                )
            )
            for piece in pieces:
                meta = base_metadata.model_copy(deep=True)
                meta.section = section_title
                chunks.append(
                    Chunk(
                        chunk_id=f"{doc_id}::{seq:03d}",
                        seq=seq,
                        text=piece,
                        metadata=meta,
                    )
                )
                seq += 1
        return chunks
