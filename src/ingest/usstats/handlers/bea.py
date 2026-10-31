"""Endpoint 1 — BEA (Ingestion Design §6.1).

GET REST returning JSON, ETL'd straight to the time-series payload (no vector
chunks). "Discovery" is the curated table list from config; each table is one
fetch. The API key (UserID query param) is kept out of the persisted document by
keying everything on a redacted ``bea://`` descriptor URL.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import UsStatsHandler
from ..parsers import parse_bea_response

log = get_logger(__name__)

PUBLICATION = "BEA NIPA"


class BeaHandler(UsStatsHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        items: list[DiscoveredItem] = []
        for tbl in c.get("tables", []):
            name = tbl["name"]
            redacted = (f"bea://{c.get('dataset', 'NIPA')}/{name}"
                        f"?freq={tbl.get('frequency', 'M')}&years={c.get('years', '')}")
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=f"BEA {name} — {tbl.get('description', '')}",
                url=redacted, doc_type="bea_table", chunking="whole", priority=1,
                extra={"table": tbl, "redacted_url": redacted},
            ))
        log.info("[%s] %d BEA table(s) to refresh", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        c = self.cfg.content
        key = self._secret(c.get("api_key_env"))
        if not key:
            return []
        tbl = item.extra["table"]
        params = {
            "UserID": key, "method": "GetData",
            "datasetname": c.get("dataset", "NIPA"),
            "TableName": tbl["name"],
            "Frequency": tbl.get("frequency", "M"),
            "Year": c.get("years", ""),
            "ResultFormat": c.get("result_format", "JSON"),
        }
        real_url = self.cfg.discovery.url + "?" + urlencode(params)
        res = self.ctx.client.get(real_url, use_cache=False)
        if not res.ok:
            return []

        try:
            payload = json.loads(res.content)
        except json.JSONDecodeError as exc:
            log.error("[%s] BEA %s: bad JSON: %s", self.cfg.id, tbl["name"], exc)
            return []

        rows, error = parse_bea_response(payload, tbl["name"])
        if error:
            log.error("[%s] BEA %s API error: %s", self.cfg.id, tbl["name"], error)
            return []

        fields = {
            "table": tbl["name"], "dataset": c.get("dataset", "NIPA"),
            "frequency": tbl.get("frequency"), "description": tbl.get("description"),
            "observation_count": len(rows),
            "retrieval_date": self.ctx.today.isoformat(),
        }
        doc = self._finish_api_doc(
            item=item, redacted_url=item.extra["redacted_url"],
            raw_content=res.content, content_hash=res.content_hash,
            publication=PUBLICATION, timeseries=rows, fields=fields,
            raw_name=f"{tbl['name']}_{self.ctx.today.isoformat()}.json",
        )
        return [doc] if doc else []
