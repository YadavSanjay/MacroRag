"""NY Fed ACM daily term premium (spec ingest-acm-daily).

One download of the NY Fed's ``ACMTermPremium.xls`` holds the whole daily history
(sheet ``ACM Daily``). The configured series are split into one numeric document per
series per calendar year, like ``fred_daily``, so the market-episode cards can
measure them. A routine run rewrites only the current and previous year; a
backfill (``--refresh``) rewrites every year from ``backfill_start``.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ...common.http_client import sha256_hex
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, FetchResult, ParsedDocument
from ...common.spreadsheet import read_workbook
from ..common_base import UsStatsHandler
from ..parsers import parse_acm_daily

log = get_logger(__name__)

DOC_TYPE = "acm_daily_series"
PUBLICATION = "NY Fed ACM term premium (daily)"


class AcmDailyHandler(UsStatsHandler):
    """NY Fed ACM daily term premium (spec ingest-acm-daily)."""

    def discover(self) -> list[DiscoveredItem]:
        """One item: the endpoint's workbook URL."""
        return [DiscoveredItem(source_id=self.cfg.id, title=PUBLICATION,
                               url=self.cfg.discovery.url, doc_type=DOC_TYPE,
                               chunking="whole", priority=1)]

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        """Download the workbook and emit one numeric document per series per year."""
        content = self.cfg.content
        try:
            res = self.ctx.client.get(item.url, use_cache=False)
        except Exception as exc:
            log.error("[%s] ACM download failed (%s)", self.cfg.id, type(exc).__name__)
            return []
        if res.status_code != 200 or not res.content:
            log.error("[%s] ACM download returned HTTP %s", self.cfg.id, res.status_code)
            return []
        try:
            sheet = read_workbook(res.content, filename_hint=item.url).get(content["sheet"])
            if sheet is None:
                raise ValueError(f"no sheet {content['sheet']!r}")
            rows = parse_acm_daily(sheet, list(content["series"]))
        except Exception as exc:
            log.error("[%s] ACM workbook unreadable: %s", self.cfg.id, exc)
            return []

        today = self.ctx.today
        start = (str(content["backfill_start"]) if self.ctx.refresh
                 else f"{today.year - 1}-01-01")
        parts: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for r in rows:
            if r["date"] >= start:
                parts.setdefault((r["series_code"], int(r["date"][:4])), []).append(r)
        docs = []
        for (series, year), part in parts.items():
            doc = self._partition_doc(item, series, year, part)
            if doc is not None:
                docs.append(doc)
        log.info("[%s] ACM: %d rows since %s -> %d documents", self.cfg.id,
                 sum(len(p) for p in parts.values()), start, len(docs))
        return docs

    def _partition_doc(self, item: DiscoveredItem, series: str, year: int,
                       part: list[dict[str, Any]]) -> ParsedDocument | None:
        url = f"acm://{series}/{year}"
        content_hash = sha256_hex(json.dumps(part, sort_keys=True).encode())
        if self.ctx.cache.is_processed(url, content_hash):
            log.debug("[%s] unchanged, skipping: %s", self.cfg.id, url)
            return None
        doc = self._build_document(
            item=item,
            res=FetchResult(url=url, status_code=200, content_hash=content_hash),
            body_text="", publication=f"{PUBLICATION} — {series}",
            release_date=date.fromisoformat(part[-1]["date"]), chunking="whole",
            fields={"series": series, "units": "percent", "year": year,
                    "observation_count": len(part)},
            timeseries=part)
        if doc is None:
            return None
        doc.doc_id = f"acm_daily_{series.lower()}_{year}"
        return doc
