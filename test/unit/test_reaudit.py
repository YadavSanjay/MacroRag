"""Tests for the harness re-audit staleness check (`scripts/harness/check_reaudit.py`).

Date logic is driven with explicit dates, never the system clock, plus a pass over the
committed docs/harness/REAUDIT.md.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "harness"))

import check_reaudit as cr  # noqa: E402  (the script dir must be on sys.path first)


def _committed_markers() -> dict[str, str]:
    return cr.parse_markers(Path(cr.REAUDIT_DOC).read_text(encoding="utf-8"))


def test_parse_markers() -> None:
    text = (
        "# doc\n<!-- reaudit:last=2026-10-17 -->\n"
        "<!-- reaudit:next_due=2027-01-15 -->\n<!-- reaudit:cadence_days=90 -->\n"
    )
    assert cr.parse_markers(text) == {
        "last": "2026-10-17", "next_due": "2027-01-15", "cadence_days": "90",
    }


def test_status_levels() -> None:
    due = dt.date(2027, 1, 15)
    assert cr.status(due, dt.date(2026, 10, 17)) == ("ok", 90)
    assert cr.status(due, dt.date(2027, 1, 1))[0] == "due-soon"  # 14 days left
    assert cr.status(due, dt.date(2027, 1, 15)) == ("due-soon", 0)
    assert cr.status(due, dt.date(2027, 1, 16)) == ("overdue", -1)


def test_consistency_warnings() -> None:
    ok = {"last": "2026-10-17", "next_due": "2027-01-15", "cadence_days": "90"}
    assert cr.consistency_warnings(ok) == []
    off = {"last": "2026-10-17", "next_due": "2026-12-01", "cadence_days": "90"}
    assert any("cadence_days" in w for w in cr.consistency_warnings(off))
    inverted = {"last": "2026-10-17", "next_due": "2026-10-01", "cadence_days": "-16"}
    assert any("not after" in w for w in cr.consistency_warnings(inverted))
    assert cr.consistency_warnings({"last": "2026-10-17"})


def test_committed_doc_is_consistent_and_fresh_at_its_own_date() -> None:
    markers = _committed_markers()
    assert cr.consistency_warnings(markers) == []
    last = dt.date.fromisoformat(markers["last"])
    assert cr.status(dt.date.fromisoformat(markers["next_due"]), last)[0] == "ok"


def test_gate_fails_only_when_overdue() -> None:
    # Dates are relative to the committed markers, so the test survives each re-audit.
    markers = _committed_markers()
    due = dt.date.fromisoformat(markers["next_due"])
    assert cr.main(["--gate", "--today", markers["last"]]) == 0
    assert cr.main(["--gate", "--today", due.isoformat()]) == 0
    assert cr.main(["--gate", "--strict", "--today", due.isoformat()]) == 1
    assert cr.main(["--gate", "--today", (due + dt.timedelta(days=1)).isoformat()]) == 1


def test_missing_doc_or_marker_returns_3(tmp_path: Path) -> None:
    assert cr.main(["--doc", str(tmp_path / "absent.md")]) == 3
    partial = tmp_path / "REAUDIT.md"
    partial.write_text("<!-- reaudit:last=2026-10-17 -->\n", encoding="utf-8")
    assert cr.main(["--doc", str(partial)]) == 3
