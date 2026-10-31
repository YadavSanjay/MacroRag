"""utils: text helpers + corpus loader."""

from __future__ import annotations

from utils.corpus import iter_chunk_points, iter_timeseries_rows
from utils.text import estimate_tokens, grounding_prefix, point_uuid


def test_point_uuid_is_deterministic_and_distinct():
    assert point_uuid("a::001") == point_uuid("a::001")
    assert point_uuid("a::001") != point_uuid("a::002")


def test_grounding_prefix_includes_publisher_and_date():
    p = {"publisher": "Federal Reserve", "publication": "FOMC Statement",
         "release_date": "2026-06-17", "section": "Policy"}
    assert grounding_prefix(p) == "Federal Reserve | FOMC Statement | 2026-06-17 | Policy"


def test_grounding_prefix_handles_missing_date():
    assert "n.d." in grounding_prefix({"publisher": "X", "publication": "Y"})


def test_estimate_tokens():
    assert estimate_tokens("a" * 40) == 10


def test_iter_chunk_points_sets_neighbours_and_grounding(corpus_dir):
    points = list(iter_chunk_points(corpus_dir))
    by_id = {p.chunk_id: p for p in points}
    first = by_id["dfast_scen::000"]
    assert first.payload["prev_chunk_id"] is None
    assert first.payload["next_chunk_id"] == "dfast_scen::001"
    # grounding prefix is prepended to embed_text but not the raw text
    assert first.embed_text.startswith("Federal Reserve | Dodd-Frank")
    assert first.text in first.embed_text
    assert first.payload["factor_tags"] == ["discount_rate", "equity_vol"]


def test_numeric_doc_yields_no_chunk_points(corpus_dir):
    ids = {p.doc_id for p in iter_chunk_points(corpus_dir)}
    assert "dfast_data" not in ids  # chunk-free numeric doc


def test_iter_timeseries_rows_carry_provenance(corpus_dir):
    rows = list(iter_timeseries_rows(corpus_dir))
    assert rows and all(r["doc_id"] == "dfast_data" for r in rows)
    r = rows[0]
    assert r["publisher"] == "Federal Reserve"
    assert r["variable"] == "unemployment_rate" and r["source"] == "DFAST_2026"
