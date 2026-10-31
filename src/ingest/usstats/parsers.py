"""Tier 3 parsing helpers (Ingestion Design §6.5).

Handles the cross-endpoint REST gotchas: embedded-comma numerics (BEA),
empty-string-as-null (BLS), and period-encoding heterogeneity (BEA ``2026M03`` /
``2026Q1`` / ``2026`` vs BLS ``M03`` + ``year``) — all normalised to ISO-8601
dates so the time-series store has one date convention. Also flattens the BEA
and BLS JSON response shapes into provenance-ready rows.
"""

from __future__ import annotations

import json
import math
import re
import typing
from datetime import date, datetime
from typing import Optional

_QUARTER_MONTH = {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10}
_SEMI_MONTH = {"S1": 1, "S2": 7}


# ── numeric coercion ─────────────────────────────────────────────────────────


def parse_value(raw) -> float | None:
    """Cast a data value to float, stripping thousands commas; '' / None -> None."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s == "" or s in ("(NA)", "n/a", "NA", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── period normalisation -> ISO date string ─────────────────────────────────


def normalize_period(period_raw: str, year: str | None = None) -> str | None:
    """Normalise heterogeneous period encodings to an ISO ``YYYY-MM-01`` date.

    Accepts BEA forms (``2026M03``, ``2026Q1``, ``2026``) and BLS forms
    (``M03``/``Q01``/``A01``/``S01`` paired with a separate ``year``).
    """
    if not period_raw:
        return None
    p = period_raw.strip().upper()

    # BEA combined forms: leading 4-digit year.
    m = re.match(r"^(\d{4})M(\d{2})$", p)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-01"
    m = re.match(r"^(\d{4})Q([1-4])$", p)
    if m:
        return f"{m.group(1)}-{_QUARTER_MONTH['Q' + m.group(2)]:02d}-01"
    if re.match(r"^\d{4}$", p):
        return f"{p}-01-01"

    # BLS forms: period code + separate year.
    if year:
        if (m := re.match(r"^M(\d{2})$", p)):
            mm = int(m.group(1))
            if 1 <= mm <= 12:
                return f"{year}-{mm:02d}-01"
            if mm == 13:           # M13 = BLS annual average
                return f"{year}-01-01"
        if (m := re.match(r"^Q0?([1-4])$", p)):
            return f"{year}-{_QUARTER_MONTH['Q' + m.group(1)]:02d}-01"
        if re.match(r"^A0?1$", p):
            return f"{year}-01-01"
        if (m := re.match(r"^S0?([12])$", p)):
            return f"{year}-{_SEMI_MONTH['S' + m.group(1)]:02d}-01"
    return None


# ── BEA response ETL (§6.1) ──────────────────────────────────────────────────


def parse_bea_response(payload: dict, table: str) -> tuple[list[dict], str | None]:
    """Flatten a BEA GetData JSON response to rows. Returns ``(rows, error)``."""
    results = (payload or {}).get("BEAAPI", {}).get("Results", {})
    if isinstance(results, list):
        results = results[0] if results else {}
    if results.get("Error"):
        return [], str(results["Error"])
    error = (payload or {}).get("BEAAPI", {}).get("Error")
    if error:
        return [], str(error)

    rows: list[dict] = []
    for d in results.get("Data", []) or []:
        period_raw = d.get("TimePeriod", "")
        rows.append({
            "table": table,
            "series_code": d.get("SeriesCode"),
            "line_number": d.get("LineNumber"),
            "line_description": d.get("LineDescription"),
            "metric": d.get("METRIC_NAME"),
            "unit": d.get("CL_UNIT"),
            "unit_mult": d.get("UNIT_MULT"),
            "period": normalize_period(period_raw),
            "period_raw": period_raw,
            "value": parse_value(d.get("DataValue")),
        })
    rows.sort(key=lambda r: (r.get("series_code") or "", r.get("period") or ""))
    return rows, None


# ── BLS response ETL (§6.2) ──────────────────────────────────────────────────


def parse_bls_response(payload: dict) -> tuple[list[dict], str | None]:
    """Flatten a BLS timeseries JSON response to rows. Returns ``(rows, status)``."""
    status = (payload or {}).get("status", "")
    rows: list[dict] = []
    for series in (payload or {}).get("Results", {}).get("series", []) or []:
        sid = series.get("seriesID")
        catalog = series.get("catalog") or {}
        title = catalog.get("series_title")
        for d in series.get("data", []) or []:
            calc = (d.get("calculations") or {}).get("pct_changes", {})
            year = d.get("year")
            period = d.get("period", "")
            rows.append({
                "series_id": sid,
                "series_title": title,
                "period": normalize_period(period, year),
                "period_raw": f"{year}{period}",
                "period_name": d.get("periodName"),
                "value": parse_value(d.get("value")),
                "mom_pct": parse_value(calc.get("1")),
                "yoy_pct": parse_value(calc.get("12")),
            })
    # BLS returns newest-first; sort ascending for a single store convention.
    rows.sort(key=lambda r: (r.get("series_id") or "", r.get("period") or ""))
    return rows, status


# ── CBO publication typing (§6.3) ────────────────────────────────────────────


def classify_cbo_publication(title: str) -> str:
    t = (title or "").lower()
    if "long-term budget outlook" in t:
        return "long_term_outlook"
    if "budget and economic outlook" in t:
        return "annual_outlook"
    if "monthly budget review" in t:
        return "monthly_review"
    if "current view" in t:
        return "current_view"
    if "testimony" in t:
        return "testimony"
    if "director" in t:
        return "director_statement"
    return "other"


def parse_acm_daily(grid: list[list], series: list[str]) -> list[dict[str, typing.Any]]:
    """Rows of the NY Fed ACM daily sheet for the requested series (spec ingest-acm-daily).

    Args:
        grid: The sheet as rows of cell values.
        series: The column names to read, e.g. ``["ACMTP10"]``.

    Returns:
        Per series in the given order, its rows in date order.

    Raises:
        ValueError: When the grid has no DATE header or lacks a requested column.
    """
    head = next((i for i, row in enumerate(grid)
                 if row and str(row[0]).strip() == "DATE"), None)
    if head is None:
        raise ValueError("ACM sheet: no DATE header row")
    cols = [str(c).strip() for c in grid[head]]
    missing = [s for s in series if s not in cols]
    if missing:
        raise ValueError(f"ACM sheet: missing column(s) {missing}")
    out: list[dict[str, typing.Any]] = []
    for s in series:
        j = cols.index(s)
        rows = []
        for row in grid[head + 1:]:
            day = _acm_date(row[0] if row else None)
            if day is None or j >= len(row):
                continue
            try:
                value = float(row[j])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            rows.append({"source": "NYFED_ACM", "series_code": s, "date": day.isoformat(),
                         "value": value, "units": "percent"})
        out.extend(sorted(rows, key=lambda r: r["date"]))
    return out


def _acm_date(cell: object) -> date | None:
    """An ACM DATE cell: a ``dd-Mon-yyyy`` string or a date/datetime value."""
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    if isinstance(cell, str):
        try:
            return datetime.strptime(cell.strip(), "%d-%b-%Y").date()
        except ValueError:
            return None
    return None


def parse_fred_observations(series_id: str, units: str, body: bytes) -> list[dict[str, typing.Any]]:
    """Flatten a FRED ``series/observations`` JSON response into time-series rows.

    Args:
        series_id: The FRED series id, stamped on every row.
        units: The series' units label, stamped on every row.
        body: The raw response body.

    Returns:
        One row per usable observation, sorted by date ascending.

    Raises:
        ValueError: The body is not JSON or has no ``observations`` list.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"FRED {series_id}: response is not JSON") from exc
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        raise ValueError(f"FRED {series_id}: response has no observations list")
    rows: list[dict[str, typing.Any]] = []
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        day = _fred_date(obs.get("date"))
        value = _fred_value(obs.get("value"))
        if day is None or value is None:
            continue
        rows.append({"source": "FRED", "series_code": series_id, "date": day,
                     "value": value, "units": units})
    rows.sort(key=lambda r: r["date"])
    return rows


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _fred_date(raw: object) -> str | None:
    """A strict ``YYYY-MM-DD`` calendar date, else None."""
    if not isinstance(raw, str) or not _ISO_DATE.fullmatch(raw):
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None
    return raw


def _fred_value(raw: object) -> float | None:
    """A finite float from FRED's string value ('.' marks a missing day), else None."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None
