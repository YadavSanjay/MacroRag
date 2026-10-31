"""SEP (Summary of Economic Projections) table profile (Ingestion Design §4.1).

The Fed has no CSV for the SEP, but it publishes a projections-materials HTML page
(``fomcprojtabl{YYYYMMDD}.htm``) whose first ``<table>`` is the canonical summary:
each variable (real GDP, unemployment, PCE / core PCE inflation, federal funds
rate) crossed with {Median, Central Tendency, Range} over {2026, 2027, 2028,
Longer run}, plus the prior meeting's row. We parse that structured HTML table
rather than the PDF's stacked-header version (the "prefer the structured source"
rule, same as DFAST).

Output is long, typed rows:
``{source, release, variable, horizon, statistic, value, vintage}`` where
``statistic`` is one of ``median | central_tendency_low/high | range_low/high``
and ``vintage`` is ``current`` or ``prior``.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

# Variable label (prefix, lowercased) -> stable code. Codes align with the DFAST
# scenario vocabulary where they overlap (e.g. unemployment_rate).
VARIABLE_CODES = {
    "change in real gdp": "real_gdp_growth",
    "unemployment rate": "unemployment_rate",
    "core pce inflation": "core_pce_inflation",   # check before "pce inflation"
    "pce inflation": "pce_inflation",
    "federal funds rate": "federal_funds_rate",
}

# Range separator: en/em dash, or the U+FFFD replacement char that appears when a
# Windows-1252 en-dash is mis-decoded. Only split *between digits* so a leading
# minus sign on a negative value is never treated as a separator.
_DASH = re.compile(r"(?<=\d)\s*[‒–—―�]\s*(?=-?\d)")


def _variable_code(label: str) -> str | None:
    key = re.sub(r"\s+", " ", label or "").strip().lower()
    for name, code in VARIABLE_CODES.items():
        if key.startswith(name):
            return code
    return None


def _is_prior(label: str) -> bool:
    return "projection" in (label or "").lower()


def _horizons(year_row: list[str]) -> list[str]:
    out: list[str] = []
    for h in year_row:
        h = (h or "").strip()
        if not h:
            continue
        out.append("longer_run" if "long" in h.lower() else h)
        if len(out) == 4:
            break
    return out


def _num(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _range(cell: str) -> tuple[float | None, float | None]:
    cell = (cell or "").strip()
    if not cell:
        return (None, None)
    parts = _DASH.split(cell, maxsplit=1)
    lo = _num(parts[0])
    hi = _num(parts[1]) if len(parts) > 1 else lo
    return (lo, hi)


def parse_projection_table(grid: list[list[str]], *, source: str,
                           release: date) -> list[dict]:
    """Parse the main SEP summary table (12 value columns = 3 statistics x 4
    horizons) into long rows. Prior-meeting rows inherit the variable above them.
    """
    if len(grid) < 3:
        return []
    horizons = _horizons(grid[1])          # row 1 repeats the year labels
    if len(horizons) < 4:
        return []

    rows: list[dict] = []
    current_var: str | None = None
    for raw in grid[2:]:
        label = raw[0] if raw else ""
        if label.lower().startswith("memo"):
            continue
        code = _variable_code(label)
        if code:
            current_var, vintage = code, "current"
        elif _is_prior(label) and current_var:
            code, vintage = current_var, "prior"
        else:
            continue

        values = raw[1:]
        for gi, stat in enumerate(("median", "central_tendency", "range")):
            for hi, horizon in enumerate(horizons):
                idx = gi * 4 + hi
                if idx >= len(values):
                    continue
                cell = values[idx]
                if stat == "median":
                    v = _num(cell)
                    if v is not None:
                        rows.append(_row(source, release, code, horizon,
                                         "median", v, vintage))
                else:
                    lo, hi_v = _range(cell)
                    for suffix, val in (("low", lo), ("high", hi_v)):
                        if val is not None:
                            rows.append(_row(source, release, code, horizon,
                                             f"{stat}_{suffix}", val, vintage))
    return rows


def _row(source, release, variable, horizon, statistic, value, vintage) -> dict:
    return {
        "source": source,
        "release": release.isoformat(),
        "variable": variable,
        "horizon": horizon,
        "statistic": statistic,
        "value": value,
        "vintage": vintage,
    }
