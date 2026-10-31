"""Endpoint 2 — IMF WEO + GFSR PDFs (Ingestion Design §7.2).

Multi-PDF bundle per release at the stable media base path
``.../publications/{pub}/{year}/{month}/english/{component}.pdf``. Discovery
HEAD-probes an existence sentinel (ch1) per candidate issue, then enqueues every
component that exists (components vary per release — execsum/ch2/statsappendix
are not always present). Chapter 1's Risk Assessment carries the AI-productivity
-risk language and is the highest-priority retrieval target.
"""

from __future__ import annotations

from datetime import date

from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import IntlHandler
from ..parsers import recent_release_months

log = get_logger(__name__)


class ImfReportsHandler(IntlHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        sentinel = c.get("existence_sentinel", "ch1")
        items: list[DiscoveredItem] = []
        for pub in c.get("publications", []):
            taken = 0
            for cand in recent_release_months(self.ctx.today,
                                              c.get("release_months", []),
                                              int(c.get("years_back", 1))):
                base = c["media_base"].format(pub=pub["key"], year=cand["year"],
                                              month=cand["month_name"])
                if not self._exists(base + sentinel + ".pdf"):
                    continue
                rd = date(cand["year"], cand["month"], 1)
                for comp in c.get("components", []):
                    url = base + comp + ".pdf"
                    if not self._exists(url):
                        continue
                    items.append(DiscoveredItem(
                        source_id=self.cfg.id,
                        title=f"{pub['label']} {cand['label']} — {comp}",
                        url=url, doc_type=f"{pub['key']}_{comp}",
                        chunking="pdf_sections",
                        priority=1 if comp in ("execsum", "ch1") else 2,
                        is_pdf=True,
                        extra={"release_date": rd, "publication": pub["label"],
                               "component": comp, "issue_label": cand["label"]},
                    ))
                taken += 1
                if taken >= int(c.get("max_issues_per_pub", 1)):
                    break
        log.info("[%s] discovered %d IMF report PDF(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        return self._ingest_pdf(
            item=item, publication=item.extra.get("publication", "IMF Report"),
            release_date=item.extra.get("release_date"),
            section_headings=self.cfg.content.get("section_headings"),
            extra_fields={"component": item.extra.get("component"),
                          "issue_label": item.extra.get("issue_label")},
        )
