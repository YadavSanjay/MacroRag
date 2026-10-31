"""Fixtures for the episode-card-horizons spec tests.

Everything is hand-written: FRED rows in the shape ``ingest-fred-daily`` stores them, the
expected episode cards worked out by hand from the market-episodes Definitions and this spec's
Definitions, FRED-shaped processed documents written to a temporary directory for the handler,
and card chunks / shock fields for ``check_card_values``. Fakes sit only at the I/O boundaries
a Tier 3 handler touches: the HTTP client (which refuses every request), the fetch cache and
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
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# E402 below: these imports need src on sys.path, set just above.
from ingest.common.chunking import Chunker  # noqa: E402
from ingest.common.config import ChunkingSettings  # noqa: E402

TODAY = date(2026, 10, 18)
REPO_ROOT = _ROOT
REL = 1e-9          # spec C1: floats to rel=1e-9 (hand decimals vs binary arithmetic)

# Environment variables that would switch the ingest object store away from the
# local backend (utils.objectstore._resolve_storage_env).
STORAGE_ENV = ("MACRORAG_DEPLOY_MODE", "MACRORAG_STORAGE_BACKEND", "MACRORAG_S3_BUCKET",
               "MACRORAG_S3_PREFIX", "MACRORAG_S3_REGION")

BAA_STANDIN = ("IG_OAS stand-in (BAA10Y, Moody's Baa yield minus 10-year Treasury; "
               "a yield spread, not an OAS)")


# ── the spec's Definitions, written out by hand ───────────────────────────────

def day_word(k: int) -> str:
    """Spec Definitions: `day` when k == 1, else `days`."""
    return "day" if k == 1 else "days"


def header(name: str, k: int, anchor: date) -> str:
    """Spec Definitions: the chunk header at horizon k."""
    return (f"Market episode: {name}. Realized moves over the {k} trading {day_word(k)} "
            f"after the {anchor.isoformat()} close, from FRED daily data.")


def line(label: str, end: str, unit: str, peak: str, k: int, last: str) -> str:
    """Spec Definitions: one card line at horizon k (end/peak already formatted)."""
    return (f"- {label}: {end} {unit} after {k} trading {day_word(k)} ({last}); "
            f"largest move within the window {peak} {unit}")


def row(series: str, d: str, v: float, units: str) -> dict:
    return {"source": "FRED", "series_code": series, "date": d, "value": v, "units": units}


def move(field, unit: str, end: float, peak: float, last_date: str) -> dict:
    return {"field": field, "unit": unit, "end": end, "peak": peak, "last_date": last_date}


def assert_moves(actual, expected: dict) -> None:
    """market-episodes C1 result equality: exact field/unit/last_date, floats rel=1e-9."""
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
    """Spec C1: SHA-256 hex of the chunk texts joined by a blank line."""
    return hashlib.sha256("\n\n".join(texts).encode("utf-8")).hexdigest()


def assert_card(doc, *, ep: dict, today: date, release: date, moves: dict[str, dict],
                chunks: list[tuple[int, str]], factor_tags: list[str]) -> None:
    """Every field market-episodes C2 and this spec's C1 fix, against a hand-built card.

    ``moves`` maps str(k) -> the expected episode_moves result for every horizon;
    ``chunks`` lists (horizon, text) in the expected chunk order."""
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
    assert set(doc.fields["moves"]) == set(moves), sorted(doc.fields["moves"])
    for k, expected in moves.items():
        assert_moves(doc.fields["moves"][k], expected)
    assert [c.chunk_id for c in doc.chunks] == [f"{doc_id}::{i:03d}"
                                                for i in range(len(chunks))]
    assert [c.seq for c in doc.chunks] == list(range(len(chunks)))
    assert [c.metadata.horizon_days for c in doc.chunks] == [k for k, _ in chunks]
    assert [c.text for c in doc.chunks] == [t for _, t in chunks]
    for c in doc.chunks:
        m = c.metadata
        assert m.doc_type == "market_episode"
        assert m.release_date == release
        assert m.factor_tags == factor_tags
        assert m.entity_tags == list(ep["entity_tags"])
    assert doc.content_hash == content_hash([t for _, t in chunks])


# ── the hand-worked episode: Lehman, anchor Friday 2008-09-12 ────────────────
#
# Same rows as the market-episodes worked example; changes per window row j (1-based):
# VIXCLS (pts, v_0 = 25.00 on 09-12; the 09-11 row is older and is not v_0)
#   09-15 +6.50  09-16 +5.25  09-17 +11.00  09-18 +8.00  09-19 +7.00
#   09-22 +9.00  09-23 +10.50  09-24 +9.75  09-25 +7.50  09-26 +14.25
# DGS2 (bp, v_0 = 2.21) on the same dates:
#   -41 -36 -56 -46 -6 -21 -26 -21 -16 -11
# BAA10Y (bp, v_0 = 3.20 on 09-11, 1 day before the anchor), five rows only:
#   09-15 +5  09-16 +10  09-17 +25  09-18 +20  09-19 +15   (no BAMLC0A0CM -> IG_OAS stand-in)
# DEXJPUS (pct, v_0 = 108.00), own dates from 09-16:
#   09-16 -2  09-17 -3  09-18 -1  09-19 -0.5  09-22 -2.5  09-23 -1.5  09-24 -1.75
#   09-25 -1.25  09-26 -2  09-29 -3.5
# DGS1 (not canonical) and BAMLH0A0HYM2 with units "lin" (no class) are ignored.
#
# Horizon 1:  VIX end +6.50 peak +6.50 (09-15); UST_2Y -41.0/-41.0 (09-15);
#             stand-in +5.0/+5.0 (09-15); USDJPY -2.00/-2.00 (09-16).
# Horizon 3:  VIX +11.00/+11.00 (09-17); UST_2Y -56.0/-56.0 (09-17);
#             stand-in +25.0/+25.0 (09-17); USDJPY -1.00/-3.00 (09-18).
# Horizon 5:  VIX +7.00/+11.00 (09-19); UST_2Y -6.0/-56.0 (09-19);
#             stand-in +15.0/+25.0 (09-19); USDJPY -2.50/-3.00 (09-22).
# Horizon 10: VIX +14.25/+14.25 (09-26); UST_2Y -11.0/-56.0 (09-26);
#             BAA10Y has 5 rows -> absent; USDJPY -3.50/-3.50 (09-29).
# Horizons >= 11: no series has that many rows -> no move.

_DATES = ["2008-09-15", "2008-09-16", "2008-09-17", "2008-09-18", "2008-09-19",
          "2008-09-22", "2008-09-23", "2008-09-24", "2008-09-25", "2008-09-26"]
_VIX = [31.50, 30.25, 36.00, 33.00, 32.00, 34.00, 35.50, 34.75, 32.50, 39.25]
_DGS2 = [1.80, 1.85, 1.65, 1.75, 2.15, 2.00, 1.95, 2.00, 2.05, 2.10]
_BAA = [3.25, 3.30, 3.45, 3.40, 3.35]
_JPY = [("2008-09-16", 105.84), ("2008-09-17", 104.76), ("2008-09-18", 106.92),
        ("2008-09-19", 107.46), ("2008-09-22", 105.30), ("2008-09-23", 106.38),
        ("2008-09-24", 106.11), ("2008-09-25", 106.65), ("2008-09-26", 105.84),
        ("2008-09-29", 104.22)]

_LEHMAN_ROWS: list[dict] = (
    [row("VIXCLS", "2008-09-11", 20.00, "index_points"),
     row("VIXCLS", "2008-09-12", 25.00, "index_points")]
    + [row("VIXCLS", d, v, "index_points") for d, v in zip(_DATES, _VIX, strict=True)]
    + [row("DGS2", "2008-09-12", 2.21, "percent")]
    + [row("DGS2", d, v, "percent") for d, v in zip(_DATES, _DGS2, strict=True)]
    + [row("BAA10Y", "2008-09-11", 3.20, "percent")]
    + [row("BAA10Y", d, v, "percent") for d, v in zip(_DATES, _BAA, strict=False)]
    + [row("DEXJPUS", "2008-09-12", 108.00, "jpy_per_usd")]
    + [row("DEXJPUS", d, v, "jpy_per_usd") for d, v in _JPY]
    + [row("DGS1", "2008-09-12", 2.00, "percent")]
    + [row("DGS1", d, 1.50, "percent") for d in _DATES]
    + [row("BAMLH0A0HYM2", "2008-09-12", 8.00, "lin")]
    + [row("BAMLH0A0HYM2", d, 9.00, "lin") for d in _DATES]
)

LEHMAN = {"id": "lehman", "name": "Lehman Brothers bankruptcy",
          "anchor": date(2008, 9, 12), "factor_tags": ["credit_spread", "systemic"],
          "entity_tags": []}
LEHMAN_FACTOR_TAGS = ["credit_spread", "systemic", "cross_asset_scenario"]

_VIXL, _UST2, _JPYL = "VIX (VIXCLS)", "UST_2Y (DGS2)", "USDJPY (DEXJPUS)"


def _lehman_text(k: int, lines: list[tuple[str, str, str, str, str]]) -> str:
    return "\n".join([header(LEHMAN["name"], k, LEHMAN["anchor"])]
                     + [line(lab, e, u, p, k, d) for lab, e, u, p, d in lines])


LEHMAN_MOVES = {
    1: {"VIXCLS": move("VIX", "pts", 6.50, 6.50, "2008-09-15"),
        "DGS2": move("UST_2Y", "bp", -41.0, -41.0, "2008-09-15"),
        "BAA10Y": move("IG_OAS", "bp", 5.0, 5.0, "2008-09-15"),
        "DEXJPUS": move("USDJPY", "pct", -2.00, -2.00, "2008-09-16")},
    3: {"VIXCLS": move("VIX", "pts", 11.00, 11.00, "2008-09-17"),
        "DGS2": move("UST_2Y", "bp", -56.0, -56.0, "2008-09-17"),
        "BAA10Y": move("IG_OAS", "bp", 25.0, 25.0, "2008-09-17"),
        "DEXJPUS": move("USDJPY", "pct", -1.00, -3.00, "2008-09-18")},
    5: {"VIXCLS": move("VIX", "pts", 7.00, 11.00, "2008-09-19"),
        "DGS2": move("UST_2Y", "bp", -6.0, -56.0, "2008-09-19"),
        "BAA10Y": move("IG_OAS", "bp", 15.0, 25.0, "2008-09-19"),
        "DEXJPUS": move("USDJPY", "pct", -2.50, -3.00, "2008-09-22")},
    10: {"VIXCLS": move("VIX", "pts", 14.25, 14.25, "2008-09-26"),
         "DGS2": move("UST_2Y", "bp", -11.0, -56.0, "2008-09-26"),
         "DEXJPUS": move("USDJPY", "pct", -3.50, -3.50, "2008-09-29")},
}
LEHMAN_TEXT = {
    1: _lehman_text(1, [(_VIXL, "+6.50", "pts", "+6.50", "2008-09-15"),
                        (_UST2, "-41.0", "bp", "-41.0", "2008-09-15"),
                        (BAA_STANDIN, "+5.0", "bp", "+5.0", "2008-09-15"),
                        (_JPYL, "-2.00", "pct", "-2.00", "2008-09-16")]),
    3: _lehman_text(3, [(_VIXL, "+11.00", "pts", "+11.00", "2008-09-17"),
                        (_UST2, "-56.0", "bp", "-56.0", "2008-09-17"),
                        (BAA_STANDIN, "+25.0", "bp", "+25.0", "2008-09-17"),
                        (_JPYL, "-1.00", "pct", "-3.00", "2008-09-18")]),
    5: _lehman_text(5, [(_VIXL, "+7.00", "pts", "+11.00", "2008-09-19"),
                        (_UST2, "-6.0", "bp", "-56.0", "2008-09-19"),
                        (BAA_STANDIN, "+15.0", "bp", "+25.0", "2008-09-19"),
                        (_JPYL, "-2.50", "pct", "-3.00", "2008-09-22")]),
    10: _lehman_text(10, [(_VIXL, "+14.25", "pts", "+14.25", "2008-09-26"),
                          (_UST2, "-11.0", "bp", "-56.0", "2008-09-26"),
                          (_JPYL, "-3.50", "pct", "-3.50", "2008-09-29")]),
}
# The literal 5- and 10-day texts of the graduated market-episodes card, for k != 1
# "exactly today's text".
LEHMAN_TEXT5_TODAY = "\n".join([
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


def lehman_rows() -> list[dict]:
    return [dict(r) for r in _LEHMAN_ROWS]


def lehman(horizons=None, *, drop: bool = False) -> dict:
    """The Lehman episode dict, with ``horizons`` set unless ``drop``."""
    ep = dict(LEHMAN)
    if not drop:
        ep["horizons"] = horizons
    return ep


def assert_lehman(doc, horizons: list[int], *, today: date = TODAY) -> None:
    """The Lehman card at the given (valid) horizons, by hand from the table above."""
    hs = sorted(horizons)
    with_move = [k for k in hs if LEHMAN_MOVES.get(k)]
    release = max(date.fromisoformat(m["last_date"]) for k in with_move
                  for m in LEHMAN_MOVES[k].values())
    assert_card(doc, ep=LEHMAN, today=today, release=release,
                moves={str(k): LEHMAN_MOVES.get(k, {}) for k in hs},
                chunks=[(k, LEHMAN_TEXT[k]) for k in with_move],
                factor_tags=LEHMAN_FACTOR_TAGS)


# ── committed config ──────────────────────────────────────────────────────────

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


# ── handler harness ───────────────────────────────────────────────────────────

def fred_doc(doc_id: str, rows: list[dict]) -> str:
    """A processed FRED document as JSON (the fields ParsedDocument dumps)."""
    series = rows[0]["series_code"] if rows else "X"
    return json.dumps({
        "doc_id": doc_id, "source_id": "fred_daily", "title": f"FRED {series}",
        "doc_type": "fred_daily_series", "publisher": "Federal Reserve Bank of St. Louis",
        "publication": f"FRED {series}",
        "release_date": max((r["date"] for r in rows), default=None),
        "retrieval_date": "2026-10-18", "url": f"fred://{series}",
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


def write_series(root: Path, series: str, rows: list[dict]) -> None:
    p = Path(root) / "tier3" / "fred_daily" / f"fred_daily_{series.lower()}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(fred_doc(f"fred_daily_{series.lower()}", rows), encoding="utf-8")


def committed_cards(root: Path) -> dict:
    """Run the committed endpoint through its registry handler; cards by doc_id."""
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
    return {d.doc_id: d for d in docs}


# ── check_card_values inputs (synth-card-value) ──────────────────────────────

CODE = "card_value_mismatch"


def card_chunk(cid: str, text: str):
    """A retrieved market_episode chunk carrying ``text``."""
    from utils.schema import RetrievedChunk
    payload = {"release_date": "2025-01-27", "publisher": "MacroRAG (derived from FRED)",
               "publication": "Market episode", "doc_id": cid.split("::")[0],
               "doc_type": "market_episode"}
    return RetrievedChunk(cid, text, 1.0, payload)


def sf(name: str, value: float, unit: str, cids: list[str]):
    from synthesis.schema import ShockField
    return ShockField(name, value, unit, chunk_ids=list(cids), rationale="",
                      grounding_mode="analog", validation=[])


def warns(diag) -> list[dict]:
    return [w for w in diag.validation if w.get("code") == CODE]


@pytest.fixture(scope="session")
def ech():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        TODAY=TODAY, REL=REL, BAA_STANDIN=BAA_STANDIN, CODE=CODE,
        day_word=day_word, header=header, line=line, row=row, move=move,
        assert_moves=assert_moves, content_hash=content_hash, assert_card=assert_card,
        LEHMAN=LEHMAN, LEHMAN_FACTOR_TAGS=LEHMAN_FACTOR_TAGS, LEHMAN_MOVES=LEHMAN_MOVES,
        LEHMAN_TEXT=LEHMAN_TEXT, LEHMAN_TEXT5_TODAY=LEHMAN_TEXT5_TODAY,
        lehman_rows=lehman_rows, lehman=lehman, assert_lehman=assert_lehman,
        config_episodes=config_episodes, vocabulary=vocabulary, as_date=as_date,
        write_series=write_series, committed_cards=committed_cards,
        card_chunk=card_chunk, sf=sf, warns=warns)
