"""Tier 4 helpers — release-month candidate generators for URL construction.

The BIS/BoJ/IMF PDF endpoints have deterministic URL patterns keyed on the
release month/year (§7.3–§7.5). These generators enumerate the most recent
candidate issues (newest first) so discovery can HEAD-probe and pick the latest
that exists, honouring each source's actual release calendar (e.g. BIS QR is
3×/yr Mar/Sep/Dec, not 4×, §7.6 #4).
"""

from __future__ import annotations

from datetime import date

MONTH_NAMES = ["january", "february", "march", "april", "may", "june",
               "july", "august", "september", "october", "november", "december"]


def recent_issue_yymm(today: date, months: list[int], years_back: int) -> list[dict]:
    """Candidate issues for ``qr-{YYYYMM}`` / ``gor{YYMM}`` patterns, newest first.

    Returns dicts with ``yymm`` (e.g. ``2603``), ``year``, ``month``, ``label``.
    """
    out: list[dict] = []
    for y in range(today.year, today.year - years_back - 1, -1):
        for m in sorted(months, reverse=True):
            d = date(y, m, 1)
            if d <= today:
                out.append({
                    "yymm": f"{y % 100:02d}{m:02d}", "year": y, "month": m,
                    "label": f"{MONTH_NAMES[m - 1].title()} {y}",
                })
    out.sort(key=lambda c: (c["year"], c["month"]), reverse=True)
    return out


def recent_years(today: date, years_back: int) -> list[int]:
    """Candidate report years (newest first) for ``ar{YYYY}e.pdf`` style patterns."""
    return list(range(today.year, today.year - years_back - 1, -1))


def recent_release_months(today: date, month_names: list[str],
                          years_back: int) -> list[dict]:
    """Candidate (year, month-name) releases for the IMF media-path pattern.

    ``month_names`` are lowercase English (``april`` / ``october``). Newest first.
    """
    name_to_num = {n: i + 1 for i, n in enumerate(MONTH_NAMES)}
    out: list[dict] = []
    for y in range(today.year, today.year - years_back - 1, -1):
        for name in month_names:
            mnum = name_to_num.get(name.lower())
            if not mnum:
                continue
            if date(y, mnum, 1) <= today:
                out.append({"year": y, "month_name": name.lower(), "month": mnum,
                            "label": f"{name.title()} {y}"})
    out.sort(key=lambda c: (c["year"], c["month"]), reverse=True)
    return out
