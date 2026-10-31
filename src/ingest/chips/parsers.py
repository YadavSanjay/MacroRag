"""Tier 6B parsing helpers (Ingestion Design §9B).

Federal Register rule field extraction (covered entities/technologies/countries,
transaction types) plus best-effort number extraction for the SIA/TSMC monthly
prints. Pure functions over already-fetched text/JSON.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

_COUNTRIES = {
    "PRC": ["people's republic of china", "prc", " china", "hong kong", "macau"],
    "Taiwan": ["taiwan"], "Russia": ["russia"], "Iran": ["iran"],
    "North Korea": ["north korea", "dprk"],
}
_TECH = {
    "advanced_computing_chips": ["advanced computing", "advanced-computing", "eccn 3a090", "3a090"],
    "semiconductor_manufacturing_equipment": ["semiconductor manufacturing equipment", "sme", "3b001", "3b090"],
    "ai_model_weights": ["model weights", "frontier model"],
    "hbm": ["high bandwidth memory", "high-bandwidth memory", "hbm"],
}


def parse_fr_rule(doc: dict, body_text: str, named_entities: list[str]) -> dict:
    """Flatten Federal Register metadata + scan text for chip-control fields."""
    hay = " ".join(filter(None, [doc.get("title", ""), doc.get("abstract", ""),
                                  body_text])).lower()
    fields = {
        "document_number": doc.get("document_number"),
        "fr_type": doc.get("type"),
        "publication_date": doc.get("publication_date"),
        "effective_date": doc.get("effective_on"),
        "topics": doc.get("topics") or [],
        "cfr_references": _flatten_cfr(doc.get("cfr_references")),
        "agency_names": doc.get("agency_names") or [],
    }
    fields["covered_countries"] = [
        name for name, kws in _COUNTRIES.items() if any(k in hay for k in kws)]
    fields["covered_technologies"] = [
        name for name, kws in _TECH.items() if any(k in hay for k in kws)]
    fields["covered_entities"] = [
        e for e in named_entities if e.lower() in hay]
    if "presumption of denial" in hay:
        fields["transaction_type"] = "presumption_of_denial"
    elif "case-by-case" in hay or "case by case" in hay:
        fields["transaction_type"] = "case_by_case_review"
    return {k: v for k, v in fields.items() if v not in (None, [], "")}


def _flatten_cfr(refs) -> list[str]:
    if not refs:
        return []
    out = []
    for r in refs:
        out.append(f"{r.get('title','')} CFR {r.get('part','')}".strip()
                   if isinstance(r, dict) else str(r))
    return out


def fr_publication_date(doc: dict) -> date | None:
    raw = doc.get("publication_date")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


# ── SIA / TSMC monthly print number extraction (best-effort) ─────────────────

_USD_BILLION = re.compile(r"\$([\d.,]+)\s*billion", re.IGNORECASE)
_YOY = re.compile(r"([\d.]+)\s*percent\s+(?:more|higher|increase|greater)?\s*"
                  r"(?:than|compared).{0,40}?(?:year|2\d{3})", re.IGNORECASE)
_NTD = re.compile(r"NT\$?\s*([\d.,]+)\s*billion", re.IGNORECASE)


def parse_sia_release(text: str) -> dict:
    fields: dict = {}
    if (m := _USD_BILLION.search(text)):
        fields["sales_usd_billion"] = m.group(1).replace(",", "")
    if (m := _YOY.search(text)):
        fields["yoy_pct_change"] = m.group(1)
    return fields


def parse_tsmc_release(text: str) -> dict:
    fields: dict = {}
    if (m := _NTD.search(text)):
        fields["revenue_ntd_billion"] = m.group(1).replace(",", "")
    yoy = re.search(r"increased?\s+([\d.]+)%?\s*(?:percent)?\s*year[- ]over[- ]year",
                    text, re.IGNORECASE)
    if yoy:
        fields["yoy_pct_change"] = yoy.group(1)
    return fields
