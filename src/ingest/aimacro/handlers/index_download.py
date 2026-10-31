"""GPR + EPU index downloads (Ingestion Design §9C.3, §9C.4).

Both are direct spreadsheet downloads of pure numeric series (no narrative to
chunk): GPR ships ``.xls``, EPU ``.xlsx``. One shared handler — each configured
``files[]`` entry is downloaded, parsed to rows, and emitted as a time-series
document. GPR-China and the EPU China/Taiwan columns feed the Taiwan-tail weight.
"""

from __future__ import annotations

from ...common import spreadsheet
from ...common.http_client import sha256_hex
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import AiMacroHandler

log = get_logger(__name__)


class IndexDownloadHandler(AiMacroHandler):

    def discover(self) -> list[DiscoveredItem]:
        items: list[DiscoveredItem] = []
        for entry in self.cfg.content.get("files", []):
            items.append(DiscoveredItem(
                source_id=self.cfg.id,
                title=entry.get("publication", entry["url"]),
                url=entry["url"], doc_type=f"{self.cfg.id}_index",
                chunking="whole", priority=1, extra={"file": entry},
            ))
        log.info("[%s] %d index file(s) to download", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self.ctx.client.get(item.url, use_cache=False)
        if not res.ok:
            return []
        try:
            workbook = spreadsheet.read_workbook(res.content, filename_hint=item.url)
        except Exception as exc:
            log.error("[%s] spreadsheet parse failed for %s: %s",
                      self.cfg.id, item.url, exc)
            return []

        entry = item.extra["file"]
        rows = spreadsheet.workbook_to_rows(workbook, source=self.cfg.id)
        fields = {
            "vintage": entry.get("vintage"),
            "sheets": list(workbook.keys()),
            "observation_count": len(rows),
            "retrieval_date": self.ctx.today.isoformat(),
        }
        doc = self._emit_numeric_doc(
            item=item, url=item.url, raw_content=res.content,
            content_hash=res.content_hash or sha256_hex(res.content),
            publication=entry.get("publication", self.cfg.title),
            timeseries=rows, fields=fields,
            raw_name=f"{self.cfg.id}_{entry.get('vintage','')}.bin",
        )
        return [doc] if doc else []
