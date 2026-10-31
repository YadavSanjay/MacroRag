"""Treasury-specific parsing helpers (Ingestion Design §5).

Pure functions over already-extracted text / Federal Register JSON — no I/O.
Covers the Treasury-page boilerplate strip (§5.4 FREEDOM250 banner), TIC
press-notice headline fields (§5.3), refunding headline numbers (§5.1), and
OISP rule-metadata extraction (§5.2).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

# ── §5.4 boilerplate strip ───────────────────────────────────────────────────

_BOILERPLATE = [
    re.compile(r"FREEDOM\s*250.*?(?:July\s*4,?\s*2026|Anniversary)", re.IGNORECASE | re.DOTALL),
    re.compile(r"Countdown to America'?s 250th Anniversary", re.IGNORECASE),
    re.compile(r"Official websites use \.gov.*?you've safely connected", re.IGNORECASE | re.DOTALL),
]


def strip_treasury_boilerplate(text: str) -> str:
    for pat in _BOILERPLATE:
        text = pat.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ── §5.3 TIC press notice ────────────────────────────────────────────────────

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_DATA_PERIOD = re.compile(r"data for\s+([A-Z][a-z]+\s+\d{4})")
_NEXT_RELEASE = re.compile(
    r"next .{0,40}?(?:release|report).{0,40}?scheduled for\s+"
    r"([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", re.IGNORECASE | re.DOTALL)
_NET_FLOW = re.compile(
    r"net\s+(?:TIC\s+)?(?:foreign\s+)?(?:acquisitions?|flows?|purchases?)[^$]{0,60}?"
    r"\$?([\d.,]+)\s*billion", re.IGNORECASE)


def parse_tic_press_notice(text: str) -> dict:
    fields: dict = {}
    if (m := _DATA_PERIOD.search(text)):
        fields["data_period"] = m.group(1).strip()
    if (m := _NET_FLOW.search(text)):
        fields["net_tic_flow_usd_bn"] = m.group(1).replace(",", "")
    if (m := _NEXT_RELEASE.search(text)):
        fields["next_release_date"] = m.group(1).strip()
    return fields


def expected_tic_urls(directory: str, pattern: str, today: date,
                      months_back: int) -> list[tuple[str, str]]:
    """Build candidate ``(url, release_month_label)`` for recent release months."""
    out: list[tuple[str, str]] = []
    y, m = today.year, today.month
    for _ in range(months_back + 1):
        mmm = MONTH_ABBR[m - 1]
        fname = pattern.replace("{MMM}", mmm).replace("{YYYY}", str(y))
        out.append((directory.rstrip("/") + "/" + fname, f"{mmm} {y}"))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


# ── §5.1 refunding statement ─────────────────────────────────────────────────

_ISSUANCE = re.compile(r"\$([\d.,]+)\s*billion of Treasury securities", re.IGNORECASE)
_MATURING = re.compile(r"refund approximately \$([\d.,]+)\s*billion", re.IGNORECASE)


def parse_refunding_statement(text: str) -> dict:
    fields: dict = {}
    if (m := _ISSUANCE.search(text)):
        fields["total_issuance_usd_bn"] = m.group(1).replace(",", "")
    if (m := _MATURING.search(text)):
        fields["privately_held_maturing_usd_bn"] = m.group(1).replace(",", "")
    return fields


def quarter_label(d: date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


# ── §5.2 OISP / Federal Register rule metadata ───────────────────────────────

_SECTORS = {
    "semiconductors": ["semiconductor", "microelectronic"],
    "quantum": ["quantum"],
    "artificial_intelligence": ["artificial intelligence", "ai system"],
    "hypersonics": ["hypersonic"],
    "high_performance_computing": ["high-performance computing", "supercomput"],
}
_COUNTRIES = {
    "PRC": ["people's republic of china", "prc", "china", "hong kong", "macau"],
    "Cuba": ["cuba"], "Iran": ["iran"], "North Korea": ["north korea", "dprk"],
    "Russia": ["russia"], "Venezuela": ["venezuela"],
}


def parse_oisp_document(doc: dict, body_text: str = "") -> dict:
    """Flatten Federal Register metadata + scan text for OISP-specific fields."""
    haystack = " ".join(filter(None, [
        doc.get("title", ""), doc.get("abstract", ""), body_text])).lower()

    fields: dict = {
        "document_number": doc.get("document_number"),
        "fr_type": doc.get("type"),
        "publication_date": doc.get("publication_date"),
        "effective_date": doc.get("effective_on"),
        "topics": doc.get("topics") or [],
        "cfr_references": _flatten_cfr(doc.get("cfr_references")),
        "agency_names": doc.get("agency_names") or [],
        "presidential_document_type": doc.get("presidential_document_type"),
    }
    fields["covered_sectors"] = [
        name for name, kws in _SECTORS.items() if any(k in haystack for k in kws)
    ]
    fields["countries_of_concern"] = [
        name for name, kws in _COUNTRIES.items() if any(k in haystack for k in kws)
    ]
    if "prohibited" in haystack and "notifiable" in haystack:
        fields["transaction_type"] = "prohibited+notifiable"
    elif "prohibited" in haystack:
        fields["transaction_type"] = "prohibited"
    elif "notifiable" in haystack:
        fields["transaction_type"] = "notifiable"
    return {k: v for k, v in fields.items() if v not in (None, [], "")}


def _flatten_cfr(refs) -> list[str]:
    if not refs:
        return []
    out = []
    for r in refs:
        if isinstance(r, dict):
            out.append(f"{r.get('title','')} CFR {r.get('part','')}".strip())
        else:
            out.append(str(r))
    return out


def fr_publication_date(doc: dict) -> date | None:
    raw = doc.get("publication_date")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None
