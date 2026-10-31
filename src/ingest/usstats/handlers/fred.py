"""FRED daily market series (spec ingest-fred-daily).

Each configured series is one request to the FRED ``series/observations`` API.
The rows are split into one numeric document per calendar year, keyed on a
redacted ``fred://SERIES/YEAR`` descriptor so the API key never reaches stored
data. FRED serves some series (the ICE BofA spreads) only over a rolling
three-year window, so a fetch must never replace stored history with a shorter
one: routine runs fetch only the current and previous year, and a backfill
(``--refresh``) writes an old year only when the response holds a row before
it, which proves the window did not cut into it.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import urlencode

from ...common.http_client import sha256_hex
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, FetchResult, ParsedDocument
from ...common.storage import safe_name
from ..common_base import UsStatsHandler
from ..parsers import parse_fred_observations

log = get_logger(__name__)

DOC_TYPE = "fred_daily_series"
PUBLICATION = "FRED daily series"
# The backfill_start year has no earlier row to prove it is whole, so it counts as
# whole when its first observation is on or before this day of January.
_START_YEAR_LAST_DAY = "01-10"


class FredDailyHandler(UsStatsHandler):
    """Daily FRED series for the market-episode library (spec ingest-fred-daily).

    Fetches the configured daily series from the FRED ``series/observations`` API
    and emits one numeric document per series per calendar year, keyed on a
    redacted ``fred://SERIES/YEAR`` descriptor so the API key never reaches
    stored data.
    """

    def discover(self) -> list[DiscoveredItem]:
        """One item per configured series."""
        items = []
        for entry in self.cfg.content.get("series", []):
            series, units = entry["id"], entry["units"]
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=f"FRED {series}",
                url=f"fred://{series}", doc_type=DOC_TYPE, chunking="whole",
                priority=1, extra={"series": series, "units": units},
            ))
        log.info("[%s] %d FRED series to refresh", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        """Fetch one series and emit its per-year partitions."""
        series, units = item.extra["series"], item.extra["units"]
        key = self._secret(self.cfg.content.get("api_key_env"))
        if not key:
            return []
        today = self.ctx.today
        backfill = bool(self.ctx.refresh)
        start = (str(self.cfg.content["backfill_start"]) if backfill
                 else f"{today.year - 1}-01-01")
        params = {"series_id": series, "api_key": key, "file_type": "json",
                  "observation_start": start}
        real_url = self.cfg.discovery.url + "?" + urlencode(params)
        try:
            res = self.ctx.client.get(real_url, use_cache=False)
        except Exception as exc:
            # Never log the exception text: an HTTP error message carries the URL,
            # and the URL carries the API key.
            log.error("[%s] FRED %s request failed (%s)", self.cfg.id, series,
                      type(exc).__name__)
            return []
        if res.status_code != 200 or not res.content:
            log.error("[%s] FRED %s returned HTTP %s", self.cfg.id, series,
                      res.status_code)
            return []
        try:
            rows = parse_fred_observations(series, units, res.content)
        except ValueError as exc:
            log.error("[%s] %s", self.cfg.id, exc)
            return []

        self.ctx.raw_store.save(self.tier.tier, self.cfg.id,
                                safe_name(f"{series}_{today.isoformat()}.json"),
                                res.content)
        docs = []
        start_year = int(str(self.cfg.content["backfill_start"])[:4])
        earliest = rows[0]["date"] if rows else ""
        for year, part in _partitions(rows).items():
            if not _keep(year, part, earliest=earliest, start_year=start_year,
                         today=today, backfill=backfill):
                log.info("[%s] FRED %s %d: skipped (starts %s with no earlier row; "
                         "the source window cut it)", self.cfg.id, series, year,
                         part[0]["date"])
                continue
            doc = self._partition_doc(item, series, units, year, part)
            if doc is not None:
                docs.append(doc)
        return docs

    def _partition_doc(self, item: DiscoveredItem, series: str, units: str,
                       year: int, part: list[dict[str, Any]]) -> ParsedDocument | None:
        redacted = f"fred://{series}/{year}"
        content_hash = sha256_hex(json.dumps(part, sort_keys=True).encode())
        if self.ctx.cache.is_processed(redacted, content_hash):
            log.debug("[%s] unchanged, skipping: %s", self.cfg.id, redacted)
            return None
        fields = {"series": series, "units": units, "year": year,
                  "observation_count": len(part),
                  "retrieval_date": self.ctx.today.isoformat()}
        doc = self._build_document(
            item=item,
            res=FetchResult(url=redacted, status_code=200, content_hash=content_hash),
            body_text="", publication=f"{PUBLICATION} — {series}",
            release_date=date.fromisoformat(part[-1]["date"]), chunking="whole",
            fields=fields, timeseries=part)
        if doc is None:
            return None
        doc.doc_id = f"fred_daily_{series.lower()}_{year}"
        return doc


def _partitions(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Rows grouped by calendar year, each group in date order."""
    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(int(r["date"][:4]), []).append(r)
    return out


def _keep(year: int, part: list[dict[str, Any]], *, earliest: str, start_year: int,
          today: date, backfill: bool) -> bool:
    """Whether a partition may be written without shortening stored history.

    The current and previous year are always written (they are still filling in).
    A routine run writes nothing older. A backfill writes an older year only when
    it is whole at its start: the response holds a row before it (``earliest`` is
    the response's first date), so the source window did not cut into it; or it
    is the backfill_start year, which has no earlier row and counts as whole when
    it starts by 10 January.
    """
    if year >= today.year - 1:
        return True
    if not backfill:
        return False
    if earliest < f"{year}-01-01":
        return True
    return year == start_year and part[0]["date"] <= f"{year}-{_START_YEAR_LAST_DAY}"
