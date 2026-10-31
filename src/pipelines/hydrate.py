"""Time-series hydration: when a query names a numeric series the corpus holds
(a DFAST scenario path, a SEP projection), pull the exact rows from the
TimeseriesStore and attach them to the answer (MacroRAG's analog of ARag's
table-JSON hydration). Heuristic and cheap — keyword cues -> store filters."""

from __future__ import annotations

from typing import Optional

from utils.logging import get_logger
from utils.schema import TimeseriesHit
from vectorstores.timeseries_store import TimeseriesStore

log = get_logger(__name__)

# query cue -> (source_like, scenario)
_SOURCE_CUES = {
    "dfast": ("DFAST", None),
    "stress test": ("DFAST", None),
    "severely adverse": ("DFAST", "severely_adverse"),
    "baseline scenario": ("DFAST", "baseline"),
    "sep": ("SEP", None),
    "dot plot": ("SEP", None),
    "summary of economic projections": ("SEP", None),
    "projections": ("SEP", None),
}

# query cue -> variable code(s) (aligned with dfast/sep variable vocab). A single
# scenario question typically names SEVERAL factors ("Treasury yields, equity
# volatility, credit spreads") — matching every cue (not just the first) is what
# lets the synthesizer see all the paths it must diff, instead of one variable.
_VARIABLE_CUES: dict[str, list[str]] = {
    "unemployment": ["unemployment_rate"],
    "real gdp": ["real_gdp_growth"], "gdp growth": ["real_gdp_growth"],
    "vix": ["vix"], "volatility": ["vix"], "equity vol": ["vix"],
    "bbb": ["bbb_corporate_yield"], "corporate yield": ["bbb_corporate_yield"],
    "credit spread": ["bbb_corporate_yield"], "credit": ["bbb_corporate_yield"],
    "treasury": ["treasury_5y", "treasury_10y"],
    "treasury yield": ["treasury_5y", "treasury_10y"],
    "10-year": ["treasury_10y"], "10 year": ["treasury_10y"],
    "5-year": ["treasury_5y"], "5 year": ["treasury_5y"],
    "fed funds": ["federal_funds_rate"], "federal funds": ["federal_funds_rate"],
    "pce": ["pce_inflation"],
}


def _first_cue(query: str, cues: dict) -> tuple | None:
    q = query.lower()
    for phrase, val in cues.items():
        if phrase in q:
            return (phrase, val)
    return None


def _matched_variables(query: str) -> list[str]:
    """Every variable named anywhere in the query (deduped, order-preserving)."""
    q = query.lower()
    out: list[str] = []
    for phrase, variables in _VARIABLE_CUES.items():
        if phrase in q:
            out.extend(variables)
    return list(dict.fromkeys(out))


def hydrate_timeseries(store: TimeseriesStore, query: str, *, max_series: int = 4,
                       limit: int = 200) -> list[TimeseriesHit]:
    src = _first_cue(query, _SOURCE_CUES)
    if not src:
        return []
    source_like, scenario = src[1]
    variables = _matched_variables(query)

    if variables:                       # pull EACH named variable (not just one)
        rows: list[dict] = []
        for v in variables:
            rows += store.query(source_like=source_like, scenario=scenario,
                                variable=v, limit=limit)
    else:                               # no specific variable -> the whole source
        rows = store.query(source_like=source_like, scenario=scenario, limit=limit)
    if not rows:
        return []
    # group rows by their concrete source tag (e.g. DFAST_2026, SEP_2026-06)
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.get("source") or source_like, []).append(r)
    hits = []
    for source, grp in list(groups.items())[:max_series]:
        hits.append(TimeseriesHit(source=source,
                                  publisher=grp[0].get("publisher", ""),
                                  rows=grp,
                                  note=f"matched on '{src[0]}'"
                                       + (f", variables={','.join(variables)}"
                                          if variables else "")))
    log.info("hydrated %d series for query cue '%s' (%d variables)",
             len(hits), src[0], len(variables))
    return hits
