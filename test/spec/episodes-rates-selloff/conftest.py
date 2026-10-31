"""Fixtures for the episodes-rates-selloff spec tests.

Hand-written FRED rows (spec Notes) written as processed FRED documents to a temporary
directory, and a harness that runs the committed ``market_episodes`` endpoint through the
handler its tier registry resolves. Fakes sit only at the I/O boundaries a Tier 3 handler
touches: the HTTP client (which refuses every request), the fetch cache and the raw/document
stores. ``today`` is fixed. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
from datetime import date
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# E402 below: these imports need src on sys.path, set just above.
from ingest.common.chunking import Chunker  # noqa: E402
from ingest.common.config import ChunkingSettings  # noqa: E402

TODAY = date(2026, 10, 15)
REPO_ROOT = _ROOT
REL = 1e-9          # market-episodes C1/C2: floats to rel=1e-9

STORAGE_ENV = ("MACRORAG_DEPLOY_MODE", "MACRORAG_STORAGE_BACKEND", "MACRORAG_S3_BUCKET",
               "MACRORAG_S3_PREFIX", "MACRORAG_S3_REGION")

# spec C1: the three new episodes, exactly.
EPISODES = {
    "taper_tantrum": ("Taper tantrum", date(2013, 6, 18),
                      ["forward_guidance", "term_premium", "rates_vol", "discount_rate"]),
    "cpi_shock_2022": ("June 2022 CPI shock", date(2022, 6, 9),
                       ["inflation", "discount_rate", "rates_vol"]),
    "treasury_refunding_2023": ("Treasury refunding and Fitch downgrade", date(2023, 7, 31),
                                ["fiscal", "term_premium", "rates_vol"]),
}
EPISODE_ID = "taper_tantrum"          # the C2 worked example
NAME, ANCHOR, FACTOR_TAGS = EPISODES[EPISODE_ID]
# The ten ids already configured, in committed order (market-episodes + russia_ukraine).
GRADUATED_IDS = ["lehman", "us_downgrade", "china_deval", "volmageddon", "covid",
                 "russia_ukraine", "svb", "yen_carry", "deepseek", "tariff"]

# spec Notes: the anchor row, then the 10 window rows.
DATES = ["2013-06-18", "2013-06-19", "2013-06-20", "2013-06-21", "2013-06-24", "2013-06-25",
         "2013-06-26", "2013-06-27", "2013-06-28", "2013-07-01", "2013-07-02"]
VALUES = {
    "VIXCLS": ([16.61, 16.64, 20.49, 18.90, 20.11, 18.47, 17.21, 16.86, 16.86, 16.37, 16.44],
               "index_points"),
    "DGS2": ([0.27, 0.31, 0.33, 0.38, 0.42, 0.43, 0.39, 0.36, 0.36, 0.34, 0.34], "percent"),
    "DGS10": ([2.20, 2.33, 2.41, 2.52, 2.57, 2.60, 2.55, 2.49, 2.52, 2.50, 2.48], "percent"),
    "THREEFYTP10": ([0.13, 0.19, 0.24, 0.30, 0.34, 0.36, 0.35, 0.32, 0.33, 0.32, 0.32],
                    "percent"),
}


def row(series: str, d: str, v: float, units: str) -> dict:
    return {"source": "FRED", "series_code": series, "date": d, "value": v, "units": units}


def notes_rows() -> dict[str, list[dict]]:
    """The spec's Notes table as FRED rows, per series."""
    return {s: [row(s, d, v, u) for d, v in zip(DATES, vals, strict=True)]
            for s, (vals, u) in VALUES.items()}


def move(field, unit: str, end: float, peak: float, last_date: str) -> dict:
    return {"field": field, "unit": unit, "end": end, "peak": peak, "last_date": last_date}


def content_hash(texts: list[str]) -> str:
    """market-episodes C2: SHA-256 hex of the chunk texts joined by a blank line."""
    return hashlib.sha256("\n\n".join(texts).encode("utf-8")).hexdigest()


def assert_moves(actual, expected: dict) -> None:
    assert isinstance(actual, dict), actual
    assert set(actual) == set(expected), (sorted(actual), sorted(expected))
    for s, e in expected.items():
        a = actual[s]
        assert a["field"] == e["field"], (s, a)
        assert a["unit"] == e["unit"], (s, a)
        assert a["last_date"] == e["last_date"], (s, a)
        # rel=1e-9: the spec's float tolerance (hand decimals vs binary arithmetic)
        assert a["end"] == pytest.approx(e["end"], rel=REL), (s, "end", a)
        # rel=1e-9: the spec's float tolerance (hand decimals vs binary arithmetic)
        assert a["peak"] == pytest.approx(e["peak"], rel=REL), (s, "peak", a)


def config_episodes() -> list[dict]:
    """content.episodes of the committed market_episodes endpoint (raw YAML)."""
    path = REPO_ROOT / "configs" / "ingest" / "tier3_usstats.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    eps = [ep for ep in data.get("endpoints") or []
           if isinstance(ep, dict) and ep.get("id") == "market_episodes"]
    assert len(eps) == 1, "tier3_usstats.yaml has no (single) market_episodes endpoint"
    return list((eps[0].get("content") or {}).get("episodes") or [])


def vocabulary() -> dict:
    path = REPO_ROOT / "configs" / "ingest" / "vocabulary.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def as_date(x) -> date | None:
    """An anchor as YAML gives it: a date (unquoted) or an ISO string (quoted)."""
    if isinstance(x, date):
        return x
    return date.fromisoformat(x) if isinstance(x, str) else None


def fred_doc(doc_id: str, rows: list[dict]) -> str:
    """A processed FRED document as JSON (the fields ParsedDocument dumps)."""
    series = rows[0]["series_code"] if rows else "X"
    return json.dumps({
        "doc_id": doc_id, "source_id": "fred_daily", "title": f"FRED {series}",
        "doc_type": "fred_daily_series", "publisher": "Federal Reserve Bank of St. Louis",
        "publication": f"FRED {series}",
        "release_date": max((r["date"] for r in rows), default=None),
        "retrieval_date": "2026-10-14", "url": f"fred://{series}/2022",
        "content_hash": doc_id, "licence": "public_domain_us_gov", "tier": 3, "bucket": "C",
        "raw_path": None, "fields": {}, "timeseries": rows, "chunks": [],
    })


class NoHttpClient:
    """The handler must never make an HTTP request."""

    def get(self, *args, **kwargs):
        raise AssertionError(f"unexpected HTTP GET {args!r}")

    def head(self, *args, **kwargs):
        raise AssertionError(f"unexpected HTTP HEAD {args!r}")

    def post(self, *args, **kwargs):
        raise AssertionError(f"unexpected HTTP POST {args!r}")

    def post_json(self, *args, **kwargs):
        raise AssertionError(f"unexpected HTTP POST {args!r}")


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
    """Document store rooted at ``root`` (where the processed FRED docs live)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def save(self, doc):
        return f"tier{doc.tier}/{doc.source_id}/{doc.doc_id}.json"


def write_series(root: Path, rows_by_series: dict[str, list[dict]]) -> None:
    for s, rows in rows_by_series.items():
        p = Path(root) / "tier3" / "fred_daily" / f"fred_daily_{s.lower()}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(fred_doc(f"fred_daily_{s.lower()}", rows), encoding="utf-8")


def committed_cards(root: Path) -> list:
    """Run the committed endpoint through its registry handler; return every card built."""
    from ingest.common.config import load_tier_config
    from ingest.usstats.ingestor import UsStatsIngestor

    tier = load_tier_config("tier3_usstats")
    eps = [ep for ep in tier.endpoints if ep.id == "market_episodes"]
    assert len(eps) == 1, "tier3_usstats.yaml has no market_episodes endpoint"
    cfg = eps[0]
    handler_cls = UsStatsIngestor.handler_registry.get(cfg.handler)
    assert handler_cls is not None, cfg.handler
    tier_ns = types.SimpleNamespace(tier=3, name="U.S. Statistical & Policy Agencies",
                                    publisher="U.S. Government",
                                    licence="public_domain_us_gov", endpoints=[cfg])
    ctx = types.SimpleNamespace(
        tier=tier_ns, client=NoHttpClient(), cache=FakeCache(), raw_store=FakeRawStore(),
        doc_store=FakeDocStore(root), chunker=Chunker(ChunkingSettings()), today=TODAY,
        settings=types.SimpleNamespace(http=types.SimpleNamespace(user_agent="test")),
        refresh=False,
    )
    with pytest.MonkeyPatch.context() as mp:
        for name in STORAGE_ENV:
            mp.delenv(name, raising=False)
        h = handler_cls(cfg, ctx)
        docs = [d for it in h.discover() for d in h.fetch(it)]
    return docs


def committed_card(root: Path, episode_id: str = EPISODE_ID):
    """The card for ``episode_id`` (an assertion fails when there is none)."""
    docs = committed_cards(root)
    cards = [d for d in docs if d.doc_id == f"market_episode_{episode_id}"]
    assert len(cards) == 1, [d.doc_id for d in docs]
    return cards[0]


@pytest.fixture(scope="session")
def rs():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        TODAY=TODAY, REL=REL, EPISODES=EPISODES, EPISODE_ID=EPISODE_ID, NAME=NAME, ANCHOR=ANCHOR,
        FACTOR_TAGS=FACTOR_TAGS, GRADUATED_IDS=GRADUATED_IDS, DATES=DATES,
        row=row, notes_rows=notes_rows, move=move, content_hash=content_hash,
        assert_moves=assert_moves, config_episodes=config_episodes, vocabulary=vocabulary,
        as_date=as_date, write_series=write_series, committed_card=committed_card,
        committed_cards=committed_cards)
