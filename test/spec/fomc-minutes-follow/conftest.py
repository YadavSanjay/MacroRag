"""Fixtures for the fomc-minutes-follow spec tests.

Synthetic Fed pages (a minutes press release and the minutes page it links, in the
Board's ``div#article`` layout), served by a fake HTTP client that records every request.
The handler runs with the committed ``press_monetary`` endpoint config and fakes for the
cache and the stores. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import hashlib
import sys
import types
from datetime import date, datetime
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# E402 below: these imports need src on sys.path, set just above.
from ingest.common.chunking import Chunker  # noqa: E402
from ingest.common.config import ChunkingSettings, load_tier_config  # noqa: E402
from ingest.common.models import DiscoveredItem, FetchResult  # noqa: E402

TODAY = date(2026, 10, 15)
BASE = "https://www.federalreserve.gov"
RELEASE_URL = f"{BASE}/newsevents/pressreleases/monetary20260819a.htm"
MINUTES_URL = f"{BASE}/monetarypolicy/fomcminutes20260729.htm"
STATEMENT_URL = f"{BASE}/newsevents/pressreleases/monetary20260729a.htm"
NOTICE = "The minutes can be viewed on the Board's website."

RELEASE_HTML = f"""<html><body><div id="article">
<p class="article__time">August 19, 2026</p>
<h3 class="title">Minutes of the Federal Open Market Committee, July 28-29, 2026</h3>
<p>The Federal Reserve on Wednesday released the minutes of the Federal Open Market
Committee meeting that was held on July 28-29, 2026.</p>
<p>{NOTICE}</p>
<p><a href="/monetarypolicy/files/fomcminutes20260729.pdf">Minutes (PDF)</a></p>
<p><a href="/monetarypolicy/fomcminutes20260729.htm">Minutes (HTML)</a></p>
</div></body></html>""".encode()

SECTIONS = {
    "Developments in Financial Markets and Open Market Operations":
        "The manager pro tempore reported that money market conditions remained stable "
        "and that reserves stayed ample throughout the intermeeting period.",
    "Staff Review of the Economic Situation":
        "The information available at the time of the meeting indicated that real activity "
        "expanded at a moderate pace while consumer price inflation remained elevated.",
    "Staff Economic Outlook":
        "The staff projected that core inflation would ease gradually over the next two "
        "years as labor market tightness continued to unwind.",
    "Participants' Views on Current Conditions and the Economic Outlook":
        "Participants observed that inflation had moved down but remained above the "
        "Committee's longer-run goal, and that labor demand had softened.",
}
MINUTES_HTML = ("<html><body><div id=\"article\">"
                "<h3>Minutes of the Federal Open Market Committee</h3>"
                "<p>A joint meeting of the Federal Open Market Committee and the Board of "
                "Governors was held in the offices of the Board.</p>"
                + "".join(f"<h4><strong>{h}</strong></h4><p>{t}</p>" for h, t in SECTIONS.items())
                + "</div></body></html>").encode()

STATEMENT_HTML = b"""<html><body><div id="article">
<h3>Federal Reserve issues FOMC statement</h3>
<p>Recent indicators suggest that economic activity has continued to expand at a solid pace.
The Committee decided to maintain the target range for the federal funds rate.</p>
</div></body></html>"""


class FakeFedClient:
    """URL -> bytes; any other URL answers 404. Records every requested URL."""

    def __init__(self, pages: dict[str, bytes]):
        self.pages = dict(pages)
        self.requests: list[str] = []

    def get(self, url, *args, **kwargs):
        self.requests.append(str(url))
        body = self.pages.get(str(url))
        if body is None:
            return FetchResult(url=str(url), status_code=404, content=b"")
        return FetchResult(url=str(url), status_code=200, content=body,
                           content_type="text/html",
                           content_hash=hashlib.sha256(body).hexdigest())

    def head(self, *args, **kwargs):
        raise AssertionError("no HEAD expected")

    def post(self, *args, **kwargs):
        raise AssertionError("no POST expected")

    def post_json(self, *args, **kwargs):
        raise AssertionError("no POST expected")


class FakeCache:
    def is_processed(self, *args, **kwargs):
        return False

    def mark_processed(self, *args, **kwargs):
        pass

    def get(self, *args, **kwargs):
        return None

    def put(self, *args, **kwargs):
        pass


class FakeRawStore:
    def save(self, *args, **kwargs):
        return "raw/path"


class FakeDocStore:
    def __init__(self):
        self.docs: list = []

    def save(self, doc):
        self.docs.append(doc)
        return f"processed/{doc.doc_id}.json"


def handler(client: FakeFedClient):
    """PressMonetaryHandler with the committed press_monetary config."""
    from ingest.fed.handlers.press_monetary import PressMonetaryHandler
    tier = load_tier_config("tier1_fed")
    cfg = next(ep for ep in tier.endpoints if ep.id == "press_monetary")
    tier_ns = types.SimpleNamespace(tier=1, name=tier.name, publisher=tier.publisher,
                                    licence=tier.licence, endpoints=[cfg])
    ctx = types.SimpleNamespace(
        tier=tier_ns, client=client, cache=FakeCache(), raw_store=FakeRawStore(),
        doc_store=FakeDocStore(), chunker=Chunker(ChunkingSettings()), today=TODAY,
        settings=types.SimpleNamespace(http=types.SimpleNamespace(user_agent="test")),
        refresh=False,
    )
    return PressMonetaryHandler(cfg, ctx)


def minutes_item(url: str = RELEASE_URL) -> DiscoveredItem:
    return DiscoveredItem(source_id="press_monetary",
                          title="Minutes of the Federal Open Market Committee, July 28-29, 2026",
                          url=url, doc_type="fomc_minutes", chunking="sections",
                          published=datetime(2026, 8, 19, 18, 0))


def statement_item() -> DiscoveredItem:
    return DiscoveredItem(source_id="press_monetary",
                          title="Federal Reserve issues FOMC statement", url=STATEMENT_URL,
                          doc_type="fomc_statement", chunking="whole",
                          published=datetime(2026, 7, 29, 18, 0))


@pytest.fixture(scope="session")
def fm():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        BASE=BASE, RELEASE_URL=RELEASE_URL, MINUTES_URL=MINUTES_URL,
        STATEMENT_URL=STATEMENT_URL, NOTICE=NOTICE, RELEASE_HTML=RELEASE_HTML,
        MINUTES_HTML=MINUTES_HTML, STATEMENT_HTML=STATEMENT_HTML, SECTIONS=SECTIONS,
        FakeFedClient=FakeFedClient, handler=handler, minutes_item=minutes_item,
        statement_item=statement_item)
