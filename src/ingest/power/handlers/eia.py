"""EIA AEO + STEO forward-forecast APIs (Ingestion Design §9A.1, §9A.2).

GET REST returning JSON, ETL'd to the time-series payload (numeric only). One
shared handler: AEO has a ``{year}`` route (HEAD-probed newest-first), STEO is
implicitly latest. The API key is kept out of the persisted doc via a redacted
``eia://`` descriptor URL; forecast vintage (``AEO2025`` / ``STEO``) is recorded.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from ...common.config import get_secret
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import PowerHandler

log = get_logger(__name__)

PUBLICATION = "EIA Energy Outlook"


class EiaHandler(PowerHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        key = get_secret(c.get("api_key_env", "EIA_API_KEY"))
        if not key:
            log.error("[%s] missing %s; skipping", self.cfg.id, c.get("api_key_env"))
            return []
        base = self.cfg.discovery.url.rstrip("/")
        route = c["route_pattern"]
        vintage = c.get("vintage_prefix", "EIA")

        # AEO: pick the latest available year via HEAD probe.
        if "{year}" in route:
            year = self._latest_year(base, route, key, c.get("year_candidates", []))
            if year is None:
                log.warning("[%s] no AEO year available; skipping", self.cfg.id)
                return []
            route = route.replace("{year}", str(year))
            vintage = f"{vintage}{year}"

        params = _encode_params(c.get("params", {}))
        real_url = f"{base}{route}?api_key={key}&{params}"
        redacted = f"eia://{route}?{params}"
        return [DiscoveredItem(
            source_id=self.cfg.id, title=f"{self.cfg.title} [{vintage}]",
            url=redacted, doc_type=self.cfg.id, chunking="whole", priority=1,
            extra={"real_url": real_url, "vintage": vintage},
        )]

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self.ctx.client.get(item.extra["real_url"], use_cache=False)
        if not res.ok:
            return []
        try:
            payload = json.loads(res.content)
        except json.JSONDecodeError as exc:
            log.error("[%s] bad JSON: %s", self.cfg.id, exc)
            return []

        data = payload.get("response", {}).get("data", []) or []
        vintage = item.extra["vintage"]
        rows = [{**{k: row.get(k) for k in ("period", "seriesId", "seriesName",
                                            "scenario", "regionId", "regionName",
                                            "unit", "value")},
                 "forecast_vintage": vintage} for row in data]
        fields = {
            "forecast_vintage": vintage,
            "observation_count": len(rows),
            "total_available": payload.get("response", {}).get("total"),
            "retrieval_date": self.ctx.today.isoformat(),
        }
        doc = self._emit_numeric_doc(
            item=item, url=item.url, raw_content=res.content,
            content_hash=res.content_hash, publication=PUBLICATION,
            timeseries=rows, fields=fields,
            raw_name=f"{vintage}_{self.ctx.today.isoformat()}.json")
        return [doc] if doc else []

    def _latest_year(self, base, route, key, candidates):
        for year in candidates:
            url = f"{base}{route.replace('{year}', str(year))}?api_key={key}"
            if self._exists(url):
                return year
        return None


def _encode_params(params: dict) -> str:
    """Encode EIA params, expanding list values to repeated keys (facets[...][])."""
    pairs: list[tuple[str, str]] = []
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            pairs.extend((k, str(x)) for x in v)
        else:
            pairs.append((k, str(v)))
    return urlencode(pairs)
