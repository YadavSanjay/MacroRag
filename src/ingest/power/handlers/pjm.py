"""PJM Data Miner 2 — DOM / Data Center Alley (Ingestion Design §9A.4).

DOM zone covers Northern Virginia "Data Center Alley" — the densest hyperscaler
concentration on Earth. The API needs a free-account ``Ocp-Apim-Subscription-Key``.
Without that key (not provisioned by default) the handler degrades to a no-op
with a clear note; the wiring is ready for when a key is added to ``.env``.
"""

from __future__ import annotations

import json

from ...common.config import get_secret
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import PowerHandler

log = get_logger(__name__)

PUBLICATION = "PJM Data Miner 2"


class PjmHandler(PowerHandler):

    def discover(self) -> list[DiscoveredItem]:
        key = get_secret(self.cfg.content.get("api_key_env", "PJM_API_KEY"))
        if not key:
            log.warning("[%s] no PJM subscription key (%s) — Data Miner 2 needs a "
                        "free account; skipping (add key to .env to enable).",
                        self.cfg.id, self.cfg.content.get("api_key_env"))
            return []
        self._key = key
        return [DiscoveredItem(
            source_id=self.cfg.id, title=f"PJM {feed['feed']} ({feed.get('load_area','')})",
            url=f"{self.cfg.discovery.url}/{feed['feed']}",
            doc_type="pjm_feed", chunking="whole", priority=1, extra={"feed": feed})
            for feed in self.cfg.content.get("feeds", [])]

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        feed = item.extra["feed"]
        params: dict[str, object] = {"rowCount": 100}
        if feed.get("load_area"):
            params["fields"] = "datetime_beginning_utc,load_area,mw"
        try:
            res = self.ctx.client.get(
                item.url + "?" + "&".join(f"{k}={v}" for k, v in params.items()),
                use_cache=False,
                extra_headers={"Ocp-Apim-Subscription-Key": self._key})
        except Exception as exc:
            log.warning("[%s] PJM feed %s failed: %s", self.cfg.id, feed["feed"], exc)
            return []
        if not res.ok:
            return []
        try:
            payload = json.loads(res.content)
        except json.JSONDecodeError:
            return []
        rows = payload.get("items", payload if isinstance(payload, list) else [])
        if feed.get("load_area"):
            rows = [r for r in rows if r.get("load_area") == feed["load_area"]]
        doc = self._emit_numeric_doc(
            item=item, url=item.url, raw_content=res.content,
            content_hash=res.content_hash, publication=PUBLICATION,
            timeseries=rows, fields={"feed": feed["feed"],
                                     "load_area": feed.get("load_area")},
            raw_name=f"pjm_{feed['feed']}.json")
        return [doc] if doc else []
