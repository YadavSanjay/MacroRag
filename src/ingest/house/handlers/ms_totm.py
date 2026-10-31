"""Endpoint 4 — Morgan Stanley Thoughts on the Market (Ingestion Design §8.4).

Every episode page publishes a full text transcript (no audio processing). Stage
A scrapes the landing page for episode links; Stage B extracts the transcript
and preserves speaker attribution in the chunk + fields, so retrieval can weight
by analyst specialty (Tirupattur=credit vs Wilson=equity, §8.6 #5).
"""

from __future__ import annotations

from ...common import dateextract, html_extract
from ...common.logging_utils import get_logger
from ...common.models import DiscoveredItem, ParsedDocument
from ..common_base import HouseHandler
from ..parsers import extract_speakers, extract_transcript, slug_from_url

log = get_logger(__name__)

PUBLICATION = "MS Thoughts on the Market"


class MsTotmHandler(HouseHandler):

    def discover(self) -> list[DiscoveredItem]:
        res = self._fetch_discovery()
        if res is None:
            return []
        pattern = self.cfg.content.get("episode_link_pattern",
                                       r"/insights/podcasts/thoughts-on-the-market/[a-z0-9-]+")
        links = html_extract.find_links(res.content, self.cfg.discovery.url,
                                        href_pattern=pattern)
        items: list[DiscoveredItem] = []
        seen: set[str] = set()
        for url, anchor in links:
            slug = slug_from_url(url)
            # Skip the section landing page itself (no episode slug).
            if not slug or slug == "thoughts-on-the-market" or url in seen:
                continue
            seen.add(url)
            items.append(DiscoveredItem(
                source_id=self.cfg.id, title=anchor or slug, url=url,
                doc_type="ms_totm_episode", chunking="whole", priority=1,
                extra={"slug": slug},
            ))
            if len(items) >= int(self.cfg.content.get("max_episodes", 8)):
                break
        log.info("[%s] discovered %d MS episode(s)", self.cfg.id, len(items))
        return items

    def fetch(self, item: DiscoveredItem) -> list[ParsedDocument]:
        res = self._fetch_content(item.url)
        if res is None:
            return []
        body = html_extract.extract_article(res.content)
        transcript = extract_transcript(
            body, self.cfg.content.get("transcript_marker", "Transcript"))
        speakers = extract_speakers(transcript)
        fields = {
            "episode_slug": item.extra.get("slug"),
            "speakers": speakers,
            "primary_speaker": speakers[0] if speakers else None,
        }
        doc = self._build_document(
            item=item, res=res, body_text=transcript or body,
            publication=PUBLICATION,
            release_date=dateextract.extract_date_from_html(
                res.content, max_date=self.ctx.today),
            chunking="whole", fields=fields,
        )
        return [doc] if doc else []
