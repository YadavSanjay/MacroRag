"""Structured-data helpers: XML record flattening and ZIP archive handling.

Used by Tier 2 (Treasury): the Auction/Buyback schedules are published as XML
(preferred over their PDF mirrors per §5.4), and TIC monthly data ships as a ZIP
of delimited TXT / HTML files. These helpers stay schema-light — they surface
records + members generically so the downstream time-series loader maps specific
layouts without this stage hard-coding each one.
"""

from __future__ import annotations

import io
import zipfile
from collections import Counter
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from .logging_utils import get_logger

log = get_logger(__name__)


def _localname(tag: str) -> str:
    """Strip an XML namespace prefix: ``{ns}Tag`` -> ``Tag``."""
    return tag.rsplit("}", 1)[-1]


def flatten_xml_records(content: bytes, record_tag: str | None = None) -> list[dict]:
    """Flatten repeated XML elements into a list of ``{leaf_tag: text}`` dicts.

    If ``record_tag`` is given, every element with that local name is a record;
    otherwise the most common repeated element (>=2 siblings) is auto-detected.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        log.warning("XML parse failed: %s", exc)
        return []

    if record_tag is None:
        record_tag = _autodetect_record_tag(root)
    if record_tag is None:
        return []

    records: list[dict] = []
    for el in root.iter():
        if _localname(el.tag) != record_tag:
            continue
        row: dict[str, str] = {}
        for child in el:
            if len(child) == 0 and child.text and child.text.strip():
                row[_localname(child.tag)] = child.text.strip()
        # Also capture attributes (some schedules carry data as attributes).
        for k, v in el.attrib.items():
            row.setdefault(_localname(k), v)
        if row:
            records.append(row)
    log.debug("flatten_xml_records: %d '%s' records", len(records), record_tag)
    return records


def _autodetect_record_tag(root: ET.Element) -> str | None:
    """Pick the element name that repeats most as a record-like container."""
    counts: Counter[str] = Counter()
    for el in root.iter():
        children = list(el)
        if children:
            tags = [_localname(c.tag) for c in children]
            for tag, n in Counter(tags).items():
                if n >= 2:
                    counts[tag] += n
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def tables_to_rows(tables: list[list[list[str]]], *, source: str, page: int) -> list[dict]:
    """Flatten extracted PDF tables into provenance-tagged rows for the ts loader.

    Schema-light by design: preserves the raw grid + a source tag so the
    downstream time-series loader can map specific named layouts (e.g. a BoJ
    projection table) without this stage hard-coding each one.
    """
    rows: list[dict] = []
    for t_idx, table in enumerate(tables):
        if not table:
            continue
        header = list(table[0])
        for r_idx, row in enumerate(table[1:], start=1):
            rows.append({
                "source": source, "page": page, "table_index": t_idx,
                "row_index": r_idx, "header": header, "cells": list(row),
            })
    return rows


# ── ZIP archive handling (TIC monthly data) ──────────────────────────────────


def list_zip_members(content: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return zf.namelist()
    except zipfile.BadZipFile as exc:
        log.warning("bad zip: %s", exc)
        return []


def read_zip_member(content: bytes, name: str) -> bytes | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return zf.read(name)
    except (zipfile.BadZipFile, KeyError) as exc:
        log.warning("zip read failed for %s: %s", name, exc)
        return None


def extract_zip_to(content: bytes, dest_dir: Path) -> list[Path]:
    """Extract all members under ``dest_dir``; returns the written paths."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                target = dest_dir / Path(member).name
                target.write_bytes(zf.read(member))
                written.append(target)
    except zipfile.BadZipFile as exc:
        log.warning("zip extract failed: %s", exc)
    return written
