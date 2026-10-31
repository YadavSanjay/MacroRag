"""Spreadsheet readers for the Tier 6C index downloads (Ingestion Design §9C).

GPR ships as legacy ``.xls`` (read with xlrd); EPU ships as ``.xlsx`` (openpyxl).
Both flatten to provenance-tagged rows for the time-series store — these series
are pure numeric data with no narrative to chunk.
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from .logging_utils import get_logger

log = get_logger(__name__)


def read_csv(content: bytes, *, encoding: str = "utf-8-sig") -> list[list[str]]:
    """Parse CSV bytes into a row grid. ``utf-8-sig`` transparently strips a BOM
    (Fed scenario CSVs ship one); decode errors are replaced, not raised."""
    text = content.decode(encoding, "replace")
    return [row for row in csv.reader(io.StringIO(text))]


def _is_xlsx(content: bytes) -> bool:
    return content[:2] == b"PK"          # xlsx is a zip container


def _is_xls(content: bytes) -> bool:
    return content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # OLE2 magic


def read_workbook(content: bytes, *, filename_hint: str = "") -> dict[str, list[list]]:
    """Read an Excel workbook to ``{sheet_name: [[cell, ...], ...]}``.

    Detects ``.xls`` vs ``.xlsx`` by magic bytes (falling back to the filename
    hint), so callers don't need to know the format up front.
    """
    if _is_xlsx(content) or filename_hint.lower().endswith("xlsx"):
        return _read_xlsx(content)
    if _is_xls(content) or filename_hint.lower().endswith("xls"):
        return _read_xls(content)
    # Last resort: try xlsx then xls.
    try:
        return _read_xlsx(content)
    except Exception:
        return _read_xls(content)


def _read_xlsx(content: bytes) -> dict[str, list[list]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    out: dict[str, list[list]] = {}
    for ws in wb.worksheets:
        out[ws.title] = [list(row) for row in ws.iter_rows(values_only=True)]
    wb.close()
    return out


def _read_xls(content: bytes) -> dict[str, list[list]]:
    import xlrd

    book = xlrd.open_workbook(file_contents=content)
    out: dict[str, list[list]] = {}
    for sheet in book.sheets():
        out[sheet.name] = [sheet.row_values(r) for r in range(sheet.nrows)]
    return out


def workbook_to_rows(workbook: dict[str, list[list]], *, source: str,
                     sheets: list[str] | None = None,
                     header_row: int = 0) -> list[dict]:
    """Flatten a workbook to ts rows: ``{source, sheet, row_index, header, cells}``.

    ``sheets`` optionally restricts which sheets are emitted; ``header_row`` is
    the 0-based index of the header line within each sheet.
    """
    rows: list[dict] = []
    for name, grid in workbook.items():
        if sheets and name not in sheets:
            continue
        if not grid:
            continue
        header = [_cell(c) for c in grid[header_row]] if header_row < len(grid) else []
        for r_idx in range(header_row + 1, len(grid)):
            cells = [_cell(c) for c in grid[r_idx]]
            if not any(c not in ("", None) for c in cells):
                continue
            rows.append({"source": source, "sheet": name, "row_index": r_idx,
                         "header": header, "cells": cells})
    return rows


def _cell(v):
    """Normalise a cell to a JSON-serialisable scalar."""
    if v is None:
        return ""
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)
