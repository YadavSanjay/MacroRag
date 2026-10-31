"""SQLite store for the numeric docs (DFAST/SEP/BEA/EIA/GPR…) that carry a
``timeseries`` payload instead of narrative chunks.

The vector store holds prose; this holds the exact numbers. The query pipeline
hydrates an answer with matching series (the MacroRAG analog of ARag's
table-JSON hydration). Schema is flexible: a few coalesced columns for filtering
plus the full original row as JSON.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from utils.logging import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id        INTEGER PRIMARY KEY,
    doc_id    TEXT,
    source_id TEXT,
    source    TEXT,
    tier      INTEGER,
    publisher TEXT,
    variable  TEXT,
    scenario  TEXT,
    domain    TEXT,
    statistic TEXT,
    period    TEXT,
    value     REAL,
    raw       TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_source   ON observations(source);
CREATE INDEX IF NOT EXISTS ix_obs_variable ON observations(variable);
CREATE INDEX IF NOT EXISTS ix_obs_scenario ON observations(scenario);
"""


def _coalesce(row: dict, *keys: str):
    for k in keys:
        if row.get(k) not in (None, ""):
            return row[k]
    return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TimeseriesStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def build(self, rows: Iterable[dict[str, Any]], *, reset: bool = True) -> int:
        con = self._connect()
        try:
            if reset:
                con.execute("DROP TABLE IF EXISTS observations")
            con.executescript(_SCHEMA)
            n = 0
            for r in rows:
                con.execute(
                    "INSERT INTO observations (doc_id, source_id, source, tier, "
                    "publisher, variable, scenario, domain, statistic, period, value, raw) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        r.get("doc_id"), r.get("source_id"),
                        _coalesce(r, "source", "source_id"),
                        r.get("tier"), r.get("publisher"),
                        _coalesce(r, "variable", "series_code", "line_description"),
                        r.get("scenario"), r.get("domain"), r.get("statistic"),
                        _coalesce(r, "period", "date", "time", "horizon"),
                        _as_float(r.get("value")),
                        json.dumps(r, ensure_ascii=False),
                    ),
                )
                n += 1
            con.commit()
            log.info("timeseries store: %d observations -> %s", n, self.db_path)
            return n
        finally:
            con.close()

    def query(self, *, source: str | None = None, variable: str | None = None,
              scenario: str | None = None, source_like: str | None = None,
              limit: int = 500) -> list[dict[str, Any]]:
        clauses, params = [], []
        if source:
            clauses.append("source = ?"); params.append(source)
        if source_like:
            clauses.append("source LIKE ?"); params.append(f"%{source_like}%")
        if variable:
            clauses.append("variable = ?"); params.append(variable)
        if scenario:
            clauses.append("scenario = ?"); params.append(scenario)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        con = self._connect()
        try:
            cur = con.execute(
                f"SELECT * FROM observations{where} LIMIT ?", (*params, limit))
            return [dict(row) for row in cur.fetchall()]
        finally:
            con.close()

    def sources(self) -> list[str]:
        con = self._connect()
        try:
            return [r[0] for r in con.execute(
                "SELECT DISTINCT source FROM observations ORDER BY source")]
        except sqlite3.OperationalError:   # table not built yet
            return []
        finally:
            con.close()

    def scenarios(self) -> list[str]:
        """Distinct non-null scenario labels present in the store."""
        con = self._connect()
        try:
            return [r[0] for r in con.execute(
                "SELECT DISTINCT scenario FROM observations "
                "WHERE scenario IS NOT NULL AND scenario <> '' ORDER BY scenario")]
        except sqlite3.OperationalError:   # table not built yet
            return []
        finally:
            con.close()

    def groups(self, *, min_rows: int = 1) -> list[dict[str, Any]]:
        """Distinct (source, variable, scenario) triples with their row counts.

        The enumeration the self-check harness samples from (eval/selfcheck.py):
        each group is a series whose stored values are a known-good answer key."""
        con = self._connect()
        try:
            cur = con.execute(
                "SELECT source, variable, scenario, COUNT(*) n FROM observations "
                "WHERE variable IS NOT NULL AND variable <> '' "
                "GROUP BY source, variable, scenario HAVING n >= ? "
                "ORDER BY source, variable, scenario", (min_rows,))
            return [{"source": r[0], "variable": r[1], "scenario": r[2], "n": r[3]}
                    for r in cur.fetchall()]
        except sqlite3.OperationalError:   # table not built yet
            return []
        finally:
            con.close()

    def count(self) -> int:
        """Total observation count (0 if the table hasn't been built)."""
        con = self._connect()
        try:
            return int(con.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
        except sqlite3.OperationalError:   # table not built yet
            return 0
        finally:
            con.close()
