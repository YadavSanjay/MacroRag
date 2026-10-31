"""Census BTOS — AI-adoption supplement (Ingestion Design §9C.1).

The only US-gov high-frequency firm-level AI-adoption series. Census EITS APIs
return a header-row-plus-rows JSON array. The endpoint is param-sensitive, so a
non-200/empty response degrades gracefully (other 6C sources are unaffected).
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

import httpx

from ...common.config import get_secret
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import AiMacroHandler

log = get_logger(__name__)

PUBLICATION = "Census BTOS"


class CensusBtosHandler(AiMacroHandler):

    def discover(self) -> list[DiscoveredItem]:
        params = dict(self.cfg.content.get("params", {}))
        key = get_secret(self.cfg.content.get("api_key_env", "CENSUS_API_KEY"))
        if key:
            params["key"] = key
        url = self.cfg.discovery.url + "?" + urlencode(params, safe=" :*")
        redacted = self.cfg.discovery.url + "?" + urlencode(
            {k: v for k, v in params.items() if k != "key"}, safe=" :*")
        return [DiscoveredItem(
            source_id=self.cfg.id, title="Census BTOS AI supplement", url=redacted,
            doc_type="census_btos", chunking="whole", priority=1,
            extra={"real_url": url},
        )]

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        try:
            res = self.ctx.client.get(item.extra["real_url"], use_cache=False)
        except httpx.HTTPStatusError as exc:
            log.warning("[%s] BTOS request failed (HTTP %s) — eits/btos is "
                        "param-sensitive; verify variable set. Skipping.",
                        self.cfg.id, exc.response.status_code)
            return []
        if not res.ok:
            return []
        try:
            table = json.loads(res.content)
        except json.JSONDecodeError:
            log.warning("[%s] BTOS returned non-JSON; skipping.", self.cfg.id)
            return []
        if not isinstance(table, list) or len(table) < 2:
            log.warning("[%s] BTOS returned no rows; skipping.", self.cfg.id)
            return []

        header, *data = table
        rows = [dict(zip(header, r)) for r in data]
        fields = {"observation_count": len(rows),
                  "retrieval_date": self.ctx.today.isoformat()}
        doc = self._emit_numeric_doc(
            item=item, url=item.url, raw_content=res.content,
            content_hash=res.content_hash, publication=PUBLICATION,
            timeseries=rows, fields=fields,
            raw_name=f"btos_{self.ctx.today.isoformat()}.json")
        return [doc] if doc else []
