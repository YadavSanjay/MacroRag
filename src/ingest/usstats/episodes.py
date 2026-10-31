"""Market-episode cards from stored daily FRED series (spec market-episodes).

A card measures what markets actually did over the 5 and the 10 trading days (or
the episode's own ``horizons``, spec episode-card-horizons) after a named stress
episode's anchor close, so a short-horizon shock field can be
grounded on a short-horizon measurement instead of research prose. Each chunk
carries its exact ``horizon_days`` for ``synthesis.validate.check_source_horizon``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from utils.objectstore import ObjectStore

from ..common.models import Chunk, ChunkMetadata, ParsedDocument

HORIZONS = (5, 10)
DOC_TYPE = "market_episode"
SOURCE_ID = "market_episodes"
PUBLISHER = "MacroRAG (derived from FRED)"
LICENCE = "derived_fred"
SCENARIO_TAG = "cross_asset_scenario"
FRED_PREFIX = "tier3/fred_daily/"
ACM_PREFIX = "tier3/acm_daily/"     # NY Fed ACM term premium (spec ingest-acm-daily)
# The anchor row may be at most this many calendar days before the anchor.
_MAX_ANCHOR_GAP_DAYS = 7
_BAA = "BAA10Y"
_ICE_IG = "BAMLC0A0CM"
_BAA_STANDIN = ("IG_OAS stand-in (BAA10Y, Moody's Baa yield minus 10-year Treasury; "
                "a yield spread, not an OAS)")

# Canonical order: series -> (shock field, label in the card text).
CANONICAL: dict[str, tuple[str | None, str]] = {
    "VIXCLS": ("VIX", "VIX (VIXCLS)"),
    "DGS2": ("UST_2Y", "UST_2Y (DGS2)"),
    "DGS5": ("UST_5Y", "UST_5Y (DGS5)"),
    "DGS10": ("UST_10Y", "UST_10Y (DGS10)"),
    "DGS30": ("UST_30Y", "UST_30Y (DGS30)"),
    _ICE_IG: ("IG_OAS", "IG_OAS (BAMLC0A0CM)"),
    "BAMLH0A0HYM2": ("HY_OAS", "HY_OAS (BAMLH0A0HYM2)"),
    _BAA: (None, "BAA10Y (Moody's Baa yield minus 10-year Treasury)"),
    "DEXTAUS": ("USDTWD", "USDTWD (DEXTAUS)"),
    "DEXJPUS": ("USDJPY", "USDJPY (DEXJPUS)"),
    "DFII10": ("REAL_YIELD_10Y", "REAL_YIELD_10Y (DFII10, 10-year TIPS real yield)"),
    "ACMTP10": ("TERM_PREMIUM_10Y", "TERM_PREMIUM_10Y (ACMTP10, ACM 10-year term premium)"),
    "THREEFYTP10": (None, "THREEFYTP10 (Kim-Wright 10-year term premium)"),
    "SOFR": (None, "SOFR (secured overnight financing rate)"),
}
_DECIMALS = {"bp": 1, "pts": 2, "pct": 2}


def _unit(units: str) -> str | None:
    """The shock unit for a stored series' units, or None when not a known class."""
    if units == "percent":
        return "bp"
    if units == "index_points":
        return "pts"
    if units.endswith("_per_usd"):
        return "pct"
    return None


def _change(unit: str, v0: float, vk: float) -> float:
    if unit == "bp":
        return (vk - v0) * 100.0
    if unit == "pct":
        return (vk / v0 - 1.0) * 100.0
    return vk - v0


def _as_date(x: date | str) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x))


def episode_moves(rows: list[dict[str, Any]], *, anchor: date,
                  horizon: int) -> dict[str, dict[str, Any]]:
    """Realized moves of each canonical FRED series over a trading-day window.

    Args:
        rows: Stored FRED time-series rows (spec ingest-fred-daily).
        anchor: The last close before the episode's shock.
        horizon: The window length in trading days (the series' own row dates).

    Returns:
        Per series code with a move: its shock field, unit, end change, peak
        change and the window's last date (spec market-episodes, Definitions).
    """
    series: dict[str, dict[str, float]] = {}
    units: dict[str, str] = {}
    for r in rows:
        s = r.get("series_code")
        unit = _unit(str(r.get("units", "")))
        if s not in CANONICAL or unit is None:
            continue
        series.setdefault(s, {})[str(r["date"])] = float(r["value"])
        units[s] = unit

    a = anchor.isoformat()
    out: dict[str, dict[str, Any]] = {}
    for s, by_date in series.items():
        before = [d for d in by_date if d <= a]
        if not before:
            continue
        d0 = max(before)
        if (anchor - date.fromisoformat(d0)).days > _MAX_ANCHOR_GAP_DAYS:
            continue
        window = sorted(d for d in by_date if d > a)[:horizon]
        if len(window) < horizon:
            continue
        unit = units[s]
        changes = [_change(unit, by_date[d0], by_date[d]) for d in window]
        peak = changes[0]
        for c in changes[1:]:
            if abs(c) > abs(peak):
                peak = c
        out[s] = {"field": CANONICAL[s][0], "unit": unit, "end": changes[-1],
                  "peak": peak, "last_date": window[-1]}
    if _BAA in out:
        out[_BAA]["field"] = None if _ICE_IG in out else "IG_OAS"
    return out


def _horizons(episode: dict[str, Any]) -> list[int]:
    """The episode's horizons in ascending order (spec episode-card-horizons).

    Raises:
        ValueError: ``horizons`` is set but is not a non-empty list of distinct
            integers of at least 1.
    """
    if "horizons" not in episode:
        return list(HORIZONS)
    hs = episode["horizons"]
    if (not isinstance(hs, list) or not hs
            or any(type(k) is not int or k < 1 for k in hs) or len(set(hs)) != len(hs)):
        raise ValueError(f"episode {episode.get('id')!r}: horizons must be a non-empty "
                         f"list of distinct integers >= 1, got {hs!r}")
    return sorted(hs)


def _chunk_text(name: str, anchor: date, k: int, moves: dict[str, dict[str, Any]]) -> str:
    days = "day" if k == 1 else "days"
    lines = [f"Market episode: {name}. Realized moves over the {k} trading {days} after "
             f"the {anchor.isoformat()} close, from FRED daily data."]
    for s in CANONICAL:
        m = moves.get(s)
        if m is None:
            continue
        label = _BAA_STANDIN if s == _BAA and m["field"] else CANONICAL[s][1]
        u, d = m["unit"], _DECIMALS[m["unit"]]
        lines.append(f"- {label}: {m['end']:+.{d}f} {u} after {k} trading {days} "
                     f"({m['last_date']}); largest move within the window "
                     f"{m['peak']:+.{d}f} {u}")
    return "\n".join(lines)


def build_episode_document(episode: dict[str, Any], rows: list[dict[str, Any]], *,
                           today: date) -> ParsedDocument | None:
    """An episode card: one chunk per horizon (by default 5 and 10 trading days).

    Args:
        episode: The configured episode (id, name, anchor, factor_tags, entity_tags,
            optional horizons).
        rows: Stored FRED time-series rows.
        today: The retrieval date stamped on the document.

    Returns:
        The card, or None when no series has a move at any horizon.

    Raises:
        ValueError: The episode's ``horizons`` is not valid.
    """
    eid, name = str(episode["id"]), str(episode["name"])
    anchor = _as_date(episode["anchor"])
    horizons = _horizons(episode)
    moves = {k: episode_moves(rows, anchor=anchor, horizon=k) for k in horizons}
    if not any(moves.values()):
        return None

    texts = [(k, _chunk_text(name, anchor, k, moves[k])) for k in horizons if moves[k]]
    release = date.fromisoformat(max(m["last_date"] for mv in moves.values()
                                     for m in mv.values()))
    content_hash = hashlib.sha256(
        "\n\n".join(t for _, t in texts).encode("utf-8")).hexdigest()
    doc_id = f"market_episode_{eid}"
    url = f"episode://{eid}"
    publication = f"Market episode — {name}"
    factor_tags = list(episode.get("factor_tags") or [])
    if SCENARIO_TAG not in factor_tags:
        factor_tags.append(SCENARIO_TAG)
    entity_tags = list(episode.get("entity_tags") or [])

    chunks = [
        Chunk(chunk_id=f"{doc_id}::{seq:03d}", seq=seq, text=text,
              metadata=ChunkMetadata(
                  publisher=PUBLISHER, publication=publication, release_date=release,
                  retrieval_date=today, url=url, content_hash=content_hash,
                  licence=LICENCE, tier=3, bucket="C", doc_type=DOC_TYPE,
                  factor_tags=list(factor_tags), entity_tags=list(entity_tags),
                  horizon_days=k))
        for seq, (k, text) in enumerate(texts)
    ]
    return ParsedDocument(
        doc_id=doc_id, source_id=SOURCE_ID, title=publication, doc_type=DOC_TYPE,
        publisher=PUBLISHER, publication=publication, release_date=release,
        retrieval_date=today, url=url, content_hash=content_hash, licence=LICENCE,
        tier=3, bucket="C",
        fields={"episode": eid, "anchor": anchor.isoformat(),
                "moves": {str(k): moves[k] for k in horizons}},
        timeseries=[], chunks=chunks)


def read_episode_rows(store: ObjectStore) -> list[dict[str, Any]]:
    """Every stored FRED and ACM daily row the episode cards measure.

    Args:
        store: The processed-document object store.

    Returns:
        The rows under ``tier3/fred_daily/`` and ``tier3/acm_daily/``; on a duplicate
        (series, date) the later retrieval wins.
    """
    return _read_rows(store, (FRED_PREFIX, ACM_PREFIX))


def read_fred_rows(store: ObjectStore) -> list[dict[str, Any]]:
    """Every stored FRED row under ``tier3/fred_daily/`` in ``store``.

    Args:
        store: The processed-document object store.

    Returns:
        The rows; on a duplicate (series, date) the later retrieval wins.
    """
    return _read_rows(store, (FRED_PREFIX,))


def _read_rows(store: ObjectStore, prefixes: tuple[str, ...]) -> list[dict[str, Any]]:
    """Timeseries rows of every ``.json`` document under ``prefixes``; on a duplicate
    (series, date) the document retrieved later wins."""
    best: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    for prefix in prefixes:
        for key in store.iter_keys(prefix, suffix=".json"):
            text = store.get_text(key)
            if not text:
                continue
            doc = json.loads(text)
            retrieved = str(doc.get("retrieval_date") or "")
            for r in doc.get("timeseries") or []:
                k = (str(r.get("series_code")), str(r.get("date")))
                if k not in best or retrieved > best[k][0]:
                    best[k] = (retrieved, r)
    return [r for _, r in best.values()]
