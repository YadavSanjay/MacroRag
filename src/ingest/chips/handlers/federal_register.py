"""Federal Register — BIS rules + Section 232 proclamations (Ingestion Design §9B.1).

THE highest-priority catalyst feed for the whole macro RAG. Reuses the shared
:mod:`ingest.common.federal_register` client (also used by Treasury OISP, §5.2)
with per-query conditions from config. Stage B prefers the clean ``raw_text_url``
rule body and stamps covered entities / technologies / countries on every chunk.
"""

from __future__ import annotations

from ...common import federal_register
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import ChipsHandler
from ..parsers import fr_publication_date, parse_fr_rule

log = get_logger(__name__)

PUBLICATION = "Federal Register Rule"


class FederalRegisterHandler(ChipsHandler):

    def discover(self) -> list[DiscoveredItem]:
        c = self.cfg.content
        per_page = int(c.get("per_page", 50))
        items: list[DiscoveredItem] = []
        for query in c.get("queries", []):
            docs = federal_register.search(
                self.ctx.client, conditions=query.get("conditions", {}),
                per_page=per_page, max_pages=1, use_cache=not self.ctx.refresh)
            for doc in docs:
                url = (doc.get("raw_text_url") or doc.get("body_html_url")
                       or doc.get("html_url"))
                if not url:
                    continue
                items.append(DiscoveredItem(
                    source_id=self.cfg.id,
                    title=doc.get("title", "Federal Register document"),
                    url=url, doc_type=query.get("doc_type", "fr_rule"),
                    chunking="sections", priority=1,
                    extra={"fr_doc": doc},
                ))
        log.info("[%s] discovered %d Federal Register document(s)",
                 self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        if item.url.endswith(".txt") or "/text/" in item.url:
            body = federal_register.pre_text(res.content)
        else:
            from ...common import html_extract
            body = html_extract.extract_body(res.content)

        doc_meta = item.extra.get("fr_doc", {})
        fields = parse_fr_rule(doc_meta, body, self.cfg.content.get("named_entities", []))
        doc = self._build_document(
            item=item, res=res, body_text=body, publication=PUBLICATION,
            release_date=fr_publication_date(doc_meta), chunking="sections",
            fields=fields)
        return [doc] if doc else []
