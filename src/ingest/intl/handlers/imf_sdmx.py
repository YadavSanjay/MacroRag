"""Endpoint 1 — IMF SDMX 3.0 API (Ingestion Design §7.1).

The only structurally novel Tier-4 piece: a DSD-positional SDMX-JSON API. Each
configured dataflow/key is one fetch; the response is flattened to numeric rows
(no vector chunks) and the resolved dataflow version is pinned in the document
fields for reproducibility (§7.6 #2). Keys are curated in config in DSD order.
"""

from __future__ import annotations

import json

from ...common import sdmx
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, FetchResult, ParsedDocument
from ...common.storage import safe_name
from ..common_base import IntlHandler

log = get_logger(__name__)

PUBLICATION = "IMF SDMX"


class ImfSdmxHandler(IntlHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        items: list[DiscoveredItem] = []
        for flow in c.get("flows", []):
            url = sdmx.build_data_url(
                c["base"], flow["agency"], flow["dataflow"], flow.get("version", "~"),
                flow["key"], start=c.get("start_period"), end=c.get("end_period"))
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=flow.get("label", flow["dataflow"]),
                url=url, doc_type="imf_sdmx_series", chunking="whole", priority=1,
                extra={"flow": flow},
            ))
        log.info("[%s] %d SDMX dataflow(s) to refresh", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self.ctx.client.get(item.url, use_cache=False,
                                  extra_headers={"Accept": sdmx.SDMX_JSON_ACCEPT})
        if not res.ok:
            return []
        if self.ctx.cache.is_processed(item.url, res.content_hash):
            log.debug("[%s] unchanged, skipping: %s", self.cfg.id, item.url)
            return []

        try:
            payload = json.loads(res.content)
        except json.JSONDecodeError as exc:
            log.error("[%s] SDMX bad JSON: %s", self.cfg.id, exc)
            return []

        rows, version = sdmx.parse_sdmx_json(payload)
        flow = item.extra["flow"]
        if not rows:
            log.warning("[%s] %s %s: 0 observations (check DSD key order, §7.6)",
                        self.cfg.id, flow["dataflow"], flow["key"])
            return []

        self.ctx.raw_store.save(
            self.tier.tier, self.cfg.id,
            safe_name(f"{flow['dataflow']}_{flow['key']}_{self.ctx.today.isoformat()}.json"),
            res.content)
        fields = {
            "agency": flow["agency"], "dataflow": flow["dataflow"],
            "key": flow["key"], "label": flow.get("label"),
            "version_requested": flow.get("version", "~"),
            "version_resolved": version,
            "observation_count": len(rows),
            "retrieval_date": self.ctx.today.isoformat(),
        }
        synthetic = FetchResult(url=item.url, status_code=200,
                                content_hash=res.content_hash)
        doc = self._build_document(
            item=item, res=synthetic, body_text="", publication=PUBLICATION,
            release_date=None, chunking="whole", fields=fields, timeseries=rows)
        return [doc] if doc else []
