"""Fixtures for the market-episodes spec tests.

Everything is hand-written: FRED rows in the shape ``ingest-fred-daily`` stores them,
FRED-shaped processed documents (as JSON in an in-memory object store, or as files in
a temporary directory for the handler), and the expected episode cards worked out by
hand from the spec's Definitions. Fakes sit only at the I/O boundaries a Tier 3
handler touches: the HTTP client (which refuses every request), the fetch cache and
the raw/document stores. ``today`` is fixed. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import hashlib
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

TODAY = date(2026, 10, 14)
REPO_ROOT = _ROOT
REL = 1e-9          # spec C1/C2: floats to rel=1e-9 (hand values vs float arithmetic)

# Environment variables that would switch the ingest object store away from the
# local backend (utils.objectstore._resolve_storage_env).
STORAGE_ENV = ("MACRORAG_DEPLOY_MODE", "MACRORAG_STORAGE_BACKEND", "MACRORAG_S3_BUCKET",
               "MACRORAG_S3_PREFIX", "MACRORAG_S3_REGION")

HDR = ("Market episode: {name}. Realized moves over the {k} trading days after the "
       "{anchor} close, from FRED daily data.")
BAA_STANDIN = ("IG_OAS stand-in (BAA10Y, Moody's Baa yield minus 10-year Treasury; "
               "a yield spread, not an OAS)")
BAA_PLAIN = "BAA10Y (Moody's Baa yield minus 10-year Treasury)"


def row(series: str, d: str, v: float, units: str) -> dict:
    return {"source": "FRED", "series_code": series, "date": d, "value": v,
            "units": units}


def move(field, unit: str, end: float, peak: float, last_date: str) -> dict:
    return {"field": field, "unit": unit, "end": end, "peak": peak,
            "last_date": last_date}


def assert_moves(actual, expected: dict) -> None:
    """C1 result equality: same series keys, exact field/unit/last_date, floats rel=1e-9."""
    assert isinstance(actual, dict), actual
    assert set(actual) == set(expected), (sorted(actual), sorted(expected))
    for s, e in expected.items():
        a = actual[s]
        assert isinstance(a, dict), (s, a)
        assert set(a) == {"field", "unit", "end", "peak", "last_date"}, (s, a)
        assert a["field"] == e["field"], (s, a)
        assert a["unit"] == e["unit"], (s, a)
        assert a["last_date"] == e["last_date"], (s, a)
        # rel=1e-9: the spec's float tolerance (hand decimals vs binary arithmetic)
        assert a["end"] == pytest.approx(e["end"], rel=REL), (s, "end", a)
        # rel=1e-9: the spec's float tolerance (hand decimals vs binary arithmetic)
        assert a["peak"] == pytest.approx(e["peak"], rel=REL), (s, "peak", a)


def content_hash(texts: list[str]) -> str:
    """Spec C2: SHA-256 hex of the chunk texts joined by a blank line."""
    return hashlib.sha256("\n\n".join(texts).encode("utf-8")).hexdigest()


def assert_card(doc, *, ep: dict, today: date, release: date, moves5: dict,
                moves10: dict, chunks: list[tuple[int, str]], factor_tags: list[str]) -> None:
    """Every field spec C2 fixes, against a hand-built expectation."""
    from ingest.common.models import ParsedDocument
    assert isinstance(doc, ParsedDocument), type(doc)
    doc_id = f"market_episode_{ep['id']}"
    assert doc.doc_id == doc_id
    assert doc.source_id == "market_episodes"
    assert doc.doc_type == "market_episode"
    assert doc.url == f"episode://{ep['id']}"
    assert doc.publisher == "MacroRAG (derived from FRED)"
    assert doc.publication == f"Market episode — {ep['name']}"
    assert doc.licence == "derived_fred"
    assert doc.tier == 3
    assert doc.bucket == "C"
    assert doc.retrieval_date == today
    assert doc.release_date == release
    assert doc.timeseries == []
    assert set(doc.fields) == {"episode", "anchor", "moves"}, doc.fields
    assert doc.fields["episode"] == ep["id"]
    assert doc.fields["anchor"] == ep["anchor"].isoformat()
    assert set(doc.fields["moves"]) == {"5", "10"}, doc.fields["moves"]
    assert_moves(doc.fields["moves"]["5"], moves5)
    assert_moves(doc.fields["moves"]["10"], moves10)
    assert [c.chunk_id for c in doc.chunks] == [f"{doc_id}::{i:03d}"
                                                for i in range(len(chunks))]
    assert [c.text for c in doc.chunks] == [t for _, t in chunks]
    for c, (k, _) in zip(doc.chunks, chunks, strict=True):
        m = c.metadata
        assert m.horizon_days == k
        assert m.doc_type == "market_episode"
        assert m.release_date == release
        assert m.factor_tags == factor_tags
        assert m.entity_tags == list(ep["entity_tags"])
    assert doc.content_hash == content_hash([t for _, t in chunks])


# ── the shared hand-worked episode: Lehman, anchor Friday 2008-09-12 ──────────
#
# VIXCLS (index_points -> pts, change v_k - v_0), v_0 = 25.00 on 09-12. Window rows
#   09-15 31.50 (+6.50)  09-16 30.25 (+5.25)  09-17 36.00 (+11.00)  09-18 33.00 (+8.00)
#   09-19 32.00 (+7.00)  09-22 34.00 (+9.00)  09-23 35.50 (+10.50)  09-24 34.75 (+9.75)
#   09-25 32.50 (+7.50)  09-26 39.25 (+14.25)
#   k=5: end +7.00, peak +11.00, last 09-19;  k=10: end +14.25, peak +14.25, last 09-26.
#   A row 09-11 (20.00) is older than the anchor-day row and must not be v_0.
# DGS2 (percent -> bp, change (v_k - v_0) x 100), v_0 = 2.21 on 09-12. Window
#   1.80 (-41) 1.85 (-36) 1.65 (-56) 1.75 (-46) 2.15 (-6) 2.00 (-21) 1.95 (-26)
#   2.00 (-21) 2.05 (-16) 2.10 (-11) on the same dates as VIXCLS.
#   k=5: end -6.0, peak -56.0, last 09-19;  k=10: end -11.0, peak -56.0, last 09-26.
# BAA10Y (percent -> bp), no row on 09-12; latest on/before is 09-11 (1 day) = 3.20.
#   Only five rows after: 3.25 (+5) 3.30 (+10) 3.45 (+25) 3.40 (+20) 3.35 (+15).
#   k=5: end +15.0, peak +25.0, last 09-19, and BAMLC0A0CM has no move -> field
#   IG_OAS with the stand-in label;  k=10: fewer than 10 rows -> absent.
# DEXJPUS (jpy_per_usd -> pct, change (v_k / v_0 - 1) x 100), v_0 = 108.00 on 09-12,
#   own dates (no 09-15 row): 09-16 105.84 (-2)  09-17 104.76 (-3)  09-18 106.92 (-1)
#   09-19 107.46 (-0.5)  09-22 105.30 (-2.5)  09-23 106.38 (-1.5)  09-24 106.11 (-1.75)
#   09-25 106.65 (-1.25)  09-26 105.84 (-2)  09-29 104.22 (-3.5)   [108 x (1 + pct/100)]
#   k=5: end -2.50, peak -3.00, last 09-22;  k=10: end -3.50, peak -3.50, last 09-29.
# DGS1 is not in the canonical table; HY rows carry units "lin" (not a known class):
#   both ignored. Release date = latest last_date over all lines = 2008-09-29.

_DATES = ["2008-09-15", "2008-09-16", "2008-09-17", "2008-09-18", "2008-09-19",
          "2008-09-22", "2008-09-23", "2008-09-24", "2008-09-25", "2008-09-26"]
_VIX = [31.50, 30.25, 36.00, 33.00, 32.00, 34.00, 35.50, 34.75, 32.50, 39.25]
_DGS2 = [1.80, 1.85, 1.65, 1.75, 2.15, 2.00, 1.95, 2.00, 2.05, 2.10]
_BAA = [3.25, 3.30, 3.45, 3.40, 3.35]
_JPY = [("2008-09-16", 105.84), ("2008-09-17", 104.76), ("2008-09-18", 106.92),
        ("2008-09-19", 107.46), ("2008-09-22", 105.30), ("2008-09-23", 106.38),
        ("2008-09-24", 106.11), ("2008-09-25", 106.65), ("2008-09-26", 105.84),
        ("2008-09-29", 104.22)]

LEHMAN_ROWS: dict[str, list[dict]] = {
    "VIXCLS": ([row("VIXCLS", "2008-09-11", 20.00, "index_points"),
                row("VIXCLS", "2008-09-12", 25.00, "index_points")]
               + [row("VIXCLS", d, v, "index_points") for d, v in zip(_DATES, _VIX,
                                                                       strict=True)]),
    "DGS2": ([row("DGS2", "2008-09-12", 2.21, "percent")]
             + [row("DGS2", d, v, "percent") for d, v in zip(_DATES, _DGS2, strict=True)]),
    "BAA10Y": ([row("BAA10Y", "2008-09-11", 3.20, "percent")]
               + [row("BAA10Y", d, v, "percent") for d, v in zip(_DATES, _BAA,
                                                                 strict=False)]),
    "DEXJPUS": ([row("DEXJPUS", "2008-09-12", 108.00, "jpy_per_usd")]
                + [row("DEXJPUS", d, v, "jpy_per_usd") for d, v in _JPY]),
    "DGS1": ([row("DGS1", "2008-09-12", 2.00, "percent")]
             + [row("DGS1", d, 1.50, "percent") for d in _DATES]),
    "BAMLH0A0HYM2": ([row("BAMLH0A0HYM2", "2008-09-12", 8.00, "lin")]
                     + [row("BAMLH0A0HYM2", d, 9.00, "lin") for d in _DATES]),
}

LEHMAN = {"id": "lehman", "name": "Lehman Brothers bankruptcy",
          "anchor": date(2008, 9, 12), "factor_tags": ["credit_spread", "systemic"],
          "entity_tags": []}

LEHMAN_MOVES5 = {
    "VIXCLS": move("VIX", "pts", 7.00, 11.00, "2008-09-19"),
    "DGS2": move("UST_2Y", "bp", -6.0, -56.0, "2008-09-19"),
    "BAA10Y": move("IG_OAS", "bp", 15.0, 25.0, "2008-09-19"),
    "DEXJPUS": move("USDJPY", "pct", -2.50, -3.00, "2008-09-22"),
}
LEHMAN_MOVES10 = {
    "VIXCLS": move("VIX", "pts", 14.25, 14.25, "2008-09-26"),
    "DGS2": move("UST_2Y", "bp", -11.0, -56.0, "2008-09-26"),
    "DEXJPUS": move("USDJPY", "pct", -3.50, -3.50, "2008-09-29"),
}
LEHMAN_TEXT5 = "\n".join([
    "Market episode: Lehman Brothers bankruptcy. Realized moves over the 5 trading days "
    "after the 2008-09-12 close, from FRED daily data.",
    "- VIX (VIXCLS): +7.00 pts after 5 trading days (2008-09-19); "
    "largest move within the window +11.00 pts",
    "- UST_2Y (DGS2): -6.0 bp after 5 trading days (2008-09-19); "
    "largest move within the window -56.0 bp",
    "- IG_OAS stand-in (BAA10Y, Moody's Baa yield minus 10-year Treasury; a yield spread, "
    "not an OAS): +15.0 bp after 5 trading days (2008-09-19); "
    "largest move within the window +25.0 bp",
    "- USDJPY (DEXJPUS): -2.50 pct after 5 trading days (2008-09-22); "
    "largest move within the window -3.00 pct",
])
LEHMAN_TEXT10 = "\n".join([
    "Market episode: Lehman Brothers bankruptcy. Realized moves over the 10 trading days "
    "after the 2008-09-12 close, from FRED daily data.",
    "- VIX (VIXCLS): +14.25 pts after 10 trading days (2008-09-26); "
    "largest move within the window +14.25 pts",
    "- UST_2Y (DGS2): -11.0 bp after 10 trading days (2008-09-26); "
    "largest move within the window -56.0 bp",
    "- USDJPY (DEXJPUS): -3.50 pct after 10 trading days (2008-09-29); "
    "largest move within the window -3.50 pct",
])
LEHMAN_RELEASE = date(2008, 9, 29)
LEHMAN_FACTOR_TAGS = ["credit_spread", "systemic", "cross_asset_scenario"]


def lehman_rows() -> list[dict]:
    return [dict(r) for rows in LEHMAN_ROWS.values() for r in rows]


def assert_lehman_card(doc, *, today: date = TODAY) -> None:
    assert_card(doc, ep=LEHMAN, today=today, release=LEHMAN_RELEASE,
                moves5=LEHMAN_MOVES5, moves10=LEHMAN_MOVES10,
                chunks=[(5, LEHMAN_TEXT5), (10, LEHMAN_TEXT10)],
                factor_tags=LEHMAN_FACTOR_TAGS)


# ── FRED-shaped processed documents ───────────────────────────────────────────

def fred_doc(doc_id: str, retrieval: str, rows: list[dict]) -> str:
    """A processed FRED document as JSON (the fields ParsedDocument dumps)."""
    series = rows[0]["series_code"] if rows else "X"
    return json.dumps({
        "doc_id": doc_id, "source_id": "fred_daily", "title": f"FRED {series}",
        "doc_type": "fred_daily_series",
        "publisher": "Federal Reserve Bank of St. Louis",
        "publication": f"FRED {series}",
        "release_date": max((r["date"] for r in rows), default=None),
        "retrieval_date": retrieval, "url": f"fred://{series}/2008",
        "content_hash": doc_id, "licence": "public_domain_us_gov", "tier": 3,
        "bucket": "C", "raw_path": None, "fields": {}, "timeseries": rows,
        "chunks": [],
    })


# ── handler harness ───────────────────────────────────────────────────────────

class NoHttpClient:
    """The handler must never make an HTTP request (spec C3)."""

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
        self.docs: list = []

    def save(self, doc):
        self.docs.append(doc)
        return f"tier{doc.tier}/{doc.source_id}/{doc.doc_id}.json"


def write_files(root: Path, files: dict[str, str]) -> None:
    for key, text in files.items():
        p = Path(root).joinpath(*key.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def endpoint(episodes: list[dict]) -> EndpointConfig:
    return EndpointConfig(
        id="market_episodes", handler="episodes", title="Market episode cards",
        publisher="MacroRAG", bucket="C",
        discovery=DiscoveryConfig(type="api", url="episode://"),
        content={"episodes": episodes},
    )


def make_handler(cfg: EndpointConfig, root: Path, *, today: date = TODAY):
    from ingest.usstats.handlers.episodes import MarketEpisodeHandler
    tier = types.SimpleNamespace(tier=3, name="U.S. Statistical & Policy Agencies",
                                 publisher="U.S. Government",
                                 licence="public_domain_us_gov", endpoints=[cfg])
    ctx = types.SimpleNamespace(
        tier=tier, client=NoHttpClient(), cache=FakeCache(), raw_store=FakeRawStore(),
        doc_store=FakeDocStore(root), chunker=Chunker(ChunkingSettings()), today=today,
        settings=types.SimpleNamespace(http=types.SimpleNamespace(user_agent="test")),
        refresh=False,
    )
    return MarketEpisodeHandler(cfg, ctx)


def run_handler(cfg: EndpointConfig, root: Path, *, today: date = TODAY):
    """discover(), then fetch() per item, with the local object-store backend.

    Returns (items, docs-per-item)."""
    with pytest.MonkeyPatch.context() as mp:
        for name in STORAGE_ENV:
            mp.delenv(name, raising=False)
        h = make_handler(cfg, root, today=today)
        items = h.discover()
        out = [h.fetch(it) for it in items]
    return items, out


@pytest.fixture(scope="session")
def me():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        TODAY=TODAY, REPO_ROOT=REPO_ROOT, REL=REL, HDR=HDR,
        BAA_STANDIN=BAA_STANDIN, BAA_PLAIN=BAA_PLAIN,
        row=row, move=move, assert_moves=assert_moves, content_hash=content_hash,
        assert_card=assert_card, LEHMAN=LEHMAN, LEHMAN_ROWS=LEHMAN_ROWS,
        LEHMAN_MOVES5=LEHMAN_MOVES5, LEHMAN_MOVES10=LEHMAN_MOVES10,
        LEHMAN_TEXT5=LEHMAN_TEXT5, LEHMAN_TEXT10=LEHMAN_TEXT10,
        LEHMAN_RELEASE=LEHMAN_RELEASE, LEHMAN_FACTOR_TAGS=LEHMAN_FACTOR_TAGS,
        lehman_rows=lehman_rows, assert_lehman_card=assert_lehman_card,
        fred_doc=fred_doc, write_files=write_files, endpoint=endpoint,
        run_handler=run_handler, make_handler=make_handler)
