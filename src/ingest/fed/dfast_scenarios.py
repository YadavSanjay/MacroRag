"""DFAST supervisory-scenario CSV profile (Ingestion Design §4.4).

The Fed publishes the scenario variable paths as clean companion CSVs next to the
scenarios PDF — one file per (scenario x domain), e.g.
``2026_Final_Supervisory_Severely_Adverse_Domestic.csv``. Those are the exact,
machine-readable shock paths the Methodology-A engine needs, so we ingest them
directly rather than reconstructing the dense stacked-header PDF tables (the
"prefer the structured source over VLM/table-parsing" lesson).

This module is pure functions over an already-read CSV grid: identify the
scenario/domain/vintage from the filename, normalize the variable column labels
to stable codes, and melt the wide date x variable matrix into long, typed
time-series rows ``{source, vintage, scenario, domain, variable, period, value}``.
"""

from __future__ import annotations

import re
from typing import Optional

# Clean canonical codes for the key macro-shock variables; every other column
# falls back to a deterministic slug of its header label (so a new Fed column
# still gets a usable code instead of being dropped).
VARIABLE_ALIASES = {
    "3_month_treasury_rate": "treasury_3m",
    "5_year_treasury_yield": "treasury_5y",
    "10_year_treasury_yield": "treasury_10y",
    "market_volatility_index": "vix",
    "dow_jones_total_stock_market_index": "dow_jones",
}

_SCENARIO_CODES = {"historic": "historic", "baseline": "baseline",
                   "severely_adverse": "severely_adverse"}

_FILENAME_RE = re.compile(
    r"(\d{4})_(Final|Proposed)_(.+?)_(Domestic|International)\.csv$", re.IGNORECASE)
_PERIOD_RE = re.compile(r"\s*(\d{4})\s*Q([1-4])", re.IGNORECASE)


def slugify_variable(label: str) -> str:
    """Header label -> stable variable code (units in parentheses are dropped)."""
    s = re.sub(r"\(.*?\)", " ", label)
    s = s.replace("U.K.", "UK").replace("U.S.", "US")   # keep acronyms intact
    s = re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower()
    return VARIABLE_ALIASES.get(s, s)


def parse_filename(name: str) -> dict | None:
    """Pull ``{year, vintage, scenario, domain}`` from a scenario CSV filename."""
    m = _FILENAME_RE.search(name)
    if not m:
        return None
    year, vintage, scen_raw, domain = m.groups()
    scen = re.sub(r"^supervisory_", "", scen_raw.lower().replace(" ", "_")).strip("_")
    return {
        "year": int(year),
        "vintage": vintage.lower(),
        "scenario": _SCENARIO_CODES.get(scen, scen),
        "domain": domain.lower(),
    }


def parse_period(value: str) -> str | None:
    """``"2026 Q1"`` -> ``"2026-Q1"``."""
    m = _PERIOD_RE.match(value or "")
    return f"{m.group(1)}-Q{m.group(2)}" if m else None


def _to_number(value: str) -> float | None:
    s = (value or "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def build_timeseries(grid: list[list[str]], *, source: str, meta: dict) -> list[dict]:
    """Melt a wide scenario CSV grid into long, typed time-series rows.

    Layout: ``[Scenario Name, Date, <var>, <var>, ...]`` with one row per quarter.
    """
    if not grid or len(grid) < 2:
        return []
    header = grid[0]
    try:
        date_idx = next(i for i, h in enumerate(header) if (h or "").strip().lower() == "date")
    except StopIteration:
        date_idx = 1  # column 0 is "Scenario Name", 1 is the date
    var_cols = [(i, slugify_variable(h)) for i, h in enumerate(header)
                if i > date_idx and (h or "").strip()]

    rows: list[dict] = []
    for raw in grid[1:]:
        if len(raw) <= date_idx:
            continue
        period = parse_period(raw[date_idx])
        if not period:
            continue
        for i, var in var_cols:
            if i >= len(raw):
                continue
            val = _to_number(raw[i])
            if val is None:
                continue
            rows.append({
                "source": source,
                "vintage": meta["vintage"],
                "scenario": meta["scenario"],
                "domain": meta["domain"],
                "variable": var,
                "period": period,
                "value": val,
            })
    return rows
