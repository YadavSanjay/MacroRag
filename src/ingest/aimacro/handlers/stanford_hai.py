"""Stanford HAI AI Index Report (Ingestion Design §9C.2).

~400pp annual canonical AI reference at a predictable URL
(``ai_index_report_{YYYY}.pdf``). Discovery HEAD-probes recent years (newest
first) and ingests the latest that exists via the shared PDF pipeline.
"""

from __future__ import annotations

from datetime import date

from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import AiMacroHandler

log = get_logger(__name__)

PUBLICATION = "Stanford HAI AI Index"


class StanfordHaiHandler(AiMacroHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        items: list[DiscoveredItem] = []
        for year in range(self.ctx.today.year, self.ctx.today.year - int(c.get("years_back", 2)) - 1, -1):
            url = c["pdf_url_pattern"].replace("{YYYY}", str(year))
            if self.ctx.client.head(url) != 200:
                continue
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=f"{PUBLICATION} {year}", url=url,
                doc_type="stanford_ai_index", chunking="pdf_sections",
                priority=1, is_pdf=True, extra={"report_year": year},
            ))
            break   # latest edition only (bucket B)
        log.info("[%s] discovered %d AI Index edition(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        year = item.extra.get("report_year")
        return self._ingest_pdf(
            item=item, publication=PUBLICATION,
            release_date=date(year, 4, 1) if year else None,
            section_headings=self.cfg.content.get("section_headings"),
            extra_fields={"report_year": year},
        )
