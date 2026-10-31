"""Endpoint 2 — BLS (Ingestion Design §6.2).

POST REST with a JSON body (the registration key goes in the body, not the URL).
Series are batched (API max 50/request) into one document per batch; output is
numeric (time-series payload only). Idempotency/raw persistence is keyed on a
redacted ``bls://`` descriptor so the key never lands in the stored JSON.
"""

from __future__ import annotations

import json

from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import UsStatsHandler
from ..parsers import parse_bls_response

log = get_logger(__name__)

PUBLICATION = "BLS Time Series"


class BlsHandler(UsStatsHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        series = [s["id"] for s in c.get("series", [])]
        batch_size = int(c.get("batch_size", 50))
        items: list[DiscoveredItem] = []
        for i in range(0, len(series), batch_size):
            batch = series[i:i + batch_size]
            redacted = f"bls://timeseries?batch={i // batch_size}&n={len(batch)}"
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=f"BLS series batch {i // batch_size}",
                url=redacted, doc_type="bls_series", chunking="whole", priority=1,
                extra={"series": batch, "redacted_url": redacted,
                       "batch_index": i // batch_size},
            ))
        log.info("[%s] %d BLS series in %d batch(es)", self.cfg.id,
                 len(series), len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        c = self.cfg.content
        key = self._secret(c.get("api_key_env"))
        body = {
            "seriesid": item.extra["series"],
            "startyear": str(c.get("start_year", "")),
            "endyear": str(c.get("end_year", "")),
            "catalog": True,
            "calculations": True,
            "annualaverage": False,
        }
        if key:
            body["registrationkey"] = key

        res = self.ctx.client.post_json(self.cfg.discovery.url, body)
        if not res.ok:
            return []

        try:
            payload = json.loads(res.content)
        except json.JSONDecodeError as exc:
            log.error("[%s] BLS: bad JSON: %s", self.cfg.id, exc)
            return []

        rows, status = parse_bls_response(payload)
        if status != "REQUEST_SUCCEEDED":
            log.warning("[%s] BLS status=%s messages=%s", self.cfg.id, status,
                        payload.get("message"))
            if not rows:
                return []

        fields = {
            "batch_index": item.extra["batch_index"],
            "series_ids": item.extra["series"],
            "observation_count": len(rows),
            "status": status,
            "retrieval_date": self.ctx.today.isoformat(),
        }
        doc = self._finish_api_doc(
            item=item, redacted_url=item.extra["redacted_url"],
            raw_content=res.content, content_hash=res.content_hash,
            publication=PUBLICATION, timeseries=rows, fields=fields,
            raw_name=f"bls_batch{item.extra['batch_index']}_{self.ctx.today.isoformat()}.json",
        )
        return [doc] if doc else []
