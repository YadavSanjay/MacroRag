"""Fixtures for the ingest-acm-daily spec tests.

Hand-written ACM sheet grids, an ``.xlsx`` workbook built with openpyxl (sheets
``ACM Monthly`` as a decoy and ``ACM Daily``), a fake HTTP client that serves it and records
requests, and fakes for the cache and the stores. ``today`` is fixed. No network, no ``data/``,
no clock.
"""

from __future__ import annotations

import hashlib
import io
import sys
import types
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# E402 below: these imports need src on sys.path, set just above.
from ingest.common.chunking import Chunker  # noqa: E402
from ingest.common.config import ChunkingSettings, DiscoveryConfig, EndpointConfig  # noqa: E402
from ingest.common.models import FetchResult  # noqa: E402

TODAY = date(2026, 10, 15)
REPO_ROOT = _ROOT
URL = ("https://www.newyorkfed.org/medialibrary/media/research/data_indicators/"
       "ACMTermPremium.xls")
HEADER = ["DATE", "ACMY10", "ACMTP05", "ACMTP10"]


def workbook(daily_rows: list[list], *, sheet: str = "ACM Daily") -> bytes:
    """An .xlsx with a decoy monthly sheet and the daily sheet (header + rows)."""
    from openpyxl import Workbook
    wb = Workbook()
    monthly = wb.active
    monthly.title = "ACM Monthly"
    monthly.append(["DATE", "ACMTP10"])
    monthly.append(["Jan-2025", 99.0])
    daily = wb.create_sheet(sheet)
    daily.append(HEADER)
    for r in daily_rows:
        daily.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def acm_row(series: str, d: str, v: float) -> dict:
    return {"source": "NYFED_ACM", "series_code": series, "date": d, "value": v,
            "units": "percent"}


class FakeClient:
    """Serves ``pages`` (URL -> bytes); other URLs answer 404. Records (url, kwargs)."""

    def __init__(self, pages: dict[str, bytes], *, status: int = 200):
        self.pages = dict(pages)
        self.status = status
        self.requests: list[tuple[str, dict]] = []

    def get(self, url, *args, **kwargs):
        self.requests.append((str(url), dict(kwargs)))
        body = self.pages.get(str(url))
        if body is None or self.status != 200:
            return FetchResult(url=str(url), status_code=404 if body is None else self.status,
                               content=b"")
        return FetchResult(url=str(url), status_code=200, content=body,
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
    def save(self, doc):
        return f"processed/{doc.doc_id}.json"


def endpoint(*, series: list[str] | None = None, sheet: str = "ACM Daily",
             backfill_start: str = "2005-01-01") -> EndpointConfig:
    return EndpointConfig(
        id="acm_daily", handler="acm", title="NY Fed ACM term premium (daily)",
        publisher="Federal Reserve Bank of New York", bucket="C",
        discovery=DiscoveryConfig(type="api", url=URL),
        content={"sheet": sheet, "series": list(series or ["ACMTP10"]),
                 "backfill_start": backfill_start},
    )


def run(cfg: EndpointConfig, client: FakeClient, *, refresh: bool, today: date = TODAY):
    """discover() then fetch() per item; returns (items, docs)."""
    from ingest.usstats.handlers.acm import AcmDailyHandler
    tier = types.SimpleNamespace(tier=3, name="U.S. Statistical & Policy Agencies",
                                 publisher="U.S. Government",
                                 licence="public_domain_us_gov", endpoints=[cfg])
    ctx = types.SimpleNamespace(
        tier=tier, client=client, cache=FakeCache(), raw_store=FakeRawStore(),
        doc_store=FakeDocStore(), chunker=Chunker(ChunkingSettings()), today=today,
        settings=types.SimpleNamespace(http=types.SimpleNamespace(user_agent="test")),
        refresh=refresh,
    )
    h = AcmDailyHandler(cfg, ctx)
    items = h.discover()
    return items, [d for it in items for d in h.fetch(it)]


@pytest.fixture(scope="session")
def acm():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        TODAY=TODAY, REPO_ROOT=REPO_ROOT, URL=URL, HEADER=HEADER, workbook=workbook,
        acm_row=acm_row, FakeClient=FakeClient, endpoint=endpoint, run=run)
