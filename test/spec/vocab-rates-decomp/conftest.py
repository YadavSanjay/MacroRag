"""Fixtures for the vocab-rates-decomp spec tests.

Hand-written daily rows written as FRED-shaped and ACM-shaped processed documents (to an
in-memory store, or a temporary directory for the handler), and the market-episode handler
harness with fakes at the I/O boundaries. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import json
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

TODAY = date(2026, 10, 15)
REL = 1e-9          # market-episodes: floats to rel=1e-9
STORAGE_ENV = ("MACRORAG_DEPLOY_MODE", "MACRORAG_STORAGE_BACKEND", "MACRORAG_S3_BUCKET",
               "MACRORAG_S3_PREFIX", "MACRORAG_S3_REGION")
REAL_LABEL = "REAL_YIELD_10Y (DFII10, 10-year TIPS real yield)"
TP_LABEL = "TERM_PREMIUM_10Y (ACMTP10, ACM 10-year term premium)"
KW_LABEL = "THREEFYTP10 (Kim-Wright 10-year term premium)"


def row(series: str, d: str, v: float, units: str = "percent", source: str = "FRED") -> dict:
    return {"source": source, "series_code": series, "date": d, "value": v, "units": units}


def doc_json(source_id: str, doc_id: str, retrieval: str, rows: list[dict]) -> str:
    """A processed numeric document (the fields ParsedDocument dumps)."""
    return json.dumps({
        "doc_id": doc_id, "source_id": source_id, "title": doc_id, "doc_type": "series",
        "publisher": "X", "publication": doc_id,
        "release_date": max((r["date"] for r in rows), default=None),
        "retrieval_date": retrieval, "url": f"x://{doc_id}", "content_hash": doc_id,
        "licence": "public_domain_us_gov", "tier": 3, "bucket": "C", "raw_path": None,
        "fields": {}, "timeseries": rows, "chunks": [],
    })


class NoHttpClient:
    def get(self, *args, **kwargs):
        raise AssertionError(f"unexpected HTTP GET {args!r}")

    def head(self, *args, **kwargs):
        raise AssertionError("unexpected HTTP HEAD")

    def post(self, *args, **kwargs):
        raise AssertionError("unexpected HTTP POST")

    def post_json(self, *args, **kwargs):
        raise AssertionError("unexpected HTTP POST")


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
    def __init__(self, root: Path):
        self.root = Path(root)

    def save(self, doc):
        return f"tier{doc.tier}/{doc.source_id}/{doc.doc_id}.json"


def write(root: Path, files: dict[str, str]) -> None:
    for k, text in files.items():
        p = Path(root).joinpath(*k.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def run_cards(root: Path, episodes: list[dict]) -> list:
    """The market-episode handler over ``root`` for ``episodes``; every card built."""
    from ingest.usstats.handlers.episodes import MarketEpisodeHandler
    cfg = EndpointConfig(id="market_episodes", handler="episodes", title="cards",
                         publisher="MacroRAG", bucket="C",
                         discovery=DiscoveryConfig(type="api", url="episode://"),
                         content={"episodes": episodes})
    tier = types.SimpleNamespace(tier=3, name="U.S.", publisher="U.S. Government",
                                 licence="public_domain_us_gov", endpoints=[cfg])
    ctx = types.SimpleNamespace(
        tier=tier, client=NoHttpClient(), cache=FakeCache(), raw_store=FakeRawStore(),
        doc_store=FakeDocStore(root), chunker=Chunker(ChunkingSettings()), today=TODAY,
        settings=types.SimpleNamespace(http=types.SimpleNamespace(user_agent="test")),
        refresh=False)
    with pytest.MonkeyPatch.context() as mp:
        for name in STORAGE_ENV:
            mp.delenv(name, raising=False)
        h = MarketEpisodeHandler(cfg, ctx)
        return [d for it in h.discover() for d in h.fetch(it)]


@pytest.fixture(scope="session")
def vr():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(TODAY=TODAY, REL=REL, REAL_LABEL=REAL_LABEL,
                                 TP_LABEL=TP_LABEL, KW_LABEL=KW_LABEL, row=row,
                                 doc_json=doc_json, write=write, run_cards=run_cards)
