"""Harness re-audit staleness check.

Every harness component encodes an assumption about what the model can't do on its own,
and those assumptions go stale as models improve.
[docs/harness/REAUDIT.md](../../docs/harness/REAUDIT.md) holds the re-audit dates and the
component register; this script reads its markers and reports when the next re-audit is
due or overdue.

REAUDIT.md carries machine markers:

    <!-- reaudit:last=2026-10-17 -->
    <!-- reaudit:next_due=2027-01-15 -->
    <!-- reaudit:cadence_days=90 -->

Usage:
    python scripts/harness/check_reaudit.py                 # status
    python scripts/harness/check_reaudit.py --gate          # exit 1 if overdue
    python scripts/harness/check_reaudit.py --gate --strict # also exit 1 if due soon
    python scripts/harness/check_reaudit.py --today 2026-11-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REAUDIT_DOC = os.path.join(REPO_ROOT, "docs", "harness", "REAUDIT.md")

WARN_WITHIN_DAYS = 14  # the "due-soon" window before next_due
REQUIRED_MARKERS = ("last", "next_due", "cadence_days")

_MARKER = re.compile(r"<!--\s*reaudit:(\w+)=([0-9-]+)\s*-->")


def parse_markers(text: str) -> dict[str, str]:
    """Extracts the `reaudit:*` markers from REAUDIT.md.

    Args:
        text: The document text.

    Returns:
        A `{key: value}` dict, e.g. `{"last": "2026-10-17", ...}`.
    """
    return {m.group(1): m.group(2) for m in _MARKER.finditer(text)}


def status(
    next_due: dt.date, today: dt.date, warn_within_days: int = WARN_WITHIN_DAYS
) -> tuple[str, int]:
    """Classifies how close the next re-audit is.

    Args:
        next_due: The date the next re-audit is due.
        today: The as-of date.
        warn_within_days: How many days before `next_due` count as due soon.

    Returns:
        `(level, days_until_due)` where level is `ok`, `due-soon` or `overdue`; the day
        count is negative when overdue.
    """
    days = (next_due - today).days
    if days < 0:
        return "overdue", days
    if days <= warn_within_days:
        return "due-soon", days
    return "ok", days


def consistency_warnings(markers: dict[str, str]) -> list[str]:
    """Checks that `next_due` equals `last` plus `cadence_days`.

    Args:
        markers: The parsed markers.

    Returns:
        Human-readable warnings; empty when the markers agree.
    """
    try:
        last = dt.date.fromisoformat(markers["last"])
        nxt = dt.date.fromisoformat(markers["next_due"])
        cadence = int(markers["cadence_days"])
    except (KeyError, ValueError) as exc:
        return [f"missing or invalid marker: {exc}"]
    warns = []
    if nxt <= last:
        warns.append(f"next_due ({nxt}) is not after last ({last})")
    expected = last + dt.timedelta(days=cadence)
    if nxt != expected:
        warns.append(f"next_due ({nxt}) != last + cadence_days ({expected})")
    return warns


def main(argv: list[str] | None = None) -> int:
    """Runs the check.

    Args:
        argv: Command-line arguments; `sys.argv[1:]` when None.

    Returns:
        0 when fine, 1 when the gate fails, 3 when the document or a marker is missing.
    """
    ap = argparse.ArgumentParser(description="MacroRAG harness re-audit staleness check")
    ap.add_argument("--gate", action="store_true", help="exit 1 when overdue")
    ap.add_argument("--strict", action="store_true", help="with --gate, also fail when due soon")
    ap.add_argument("--today", default=None, help="evaluate as of YYYY-MM-DD (default: today)")
    ap.add_argument("--doc", default=REAUDIT_DOC)
    args = ap.parse_args(argv)

    if not os.path.isfile(args.doc):
        sys.stderr.write(f"re-audit doc not found: {args.doc}\n")
        return 3
    with open(args.doc, encoding="utf-8") as fh:
        markers = parse_markers(fh.read())
    missing = [k for k in REQUIRED_MARKERS if k not in markers]
    if missing:
        sys.stderr.write(f"REAUDIT.md is missing markers: {', '.join(missing)}\n")
        return 3

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    level, days = status(dt.date.fromisoformat(markers["next_due"]), today)
    for warning in consistency_warnings(markers):
        print(f"WARN: {warning}")
    remaining = f"{days} day(s) remaining." if days >= 0 else f"{-days} day(s) overdue."
    print(
        f"[{level.upper()}] harness re-audit: last {markers['last']}, next due "
        f"{markers['next_due']} (cadence {markers['cadence_days']}d). As of {today}: {remaining}"
    )
    if level != "ok":
        print("  Re-run the re-audit as docs/harness/REAUDIT.md describes.")
    if args.gate and (level == "overdue" or (args.strict and level == "due-soon")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
