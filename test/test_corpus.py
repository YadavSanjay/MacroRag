"""Corpus browse surface tests (file-based; no models/network).

Exercises /corpus/facets, /corpus/documents (+ filters/paging), the document
detail, and the series catalog over the synthetic processed corpus.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from utils.config import RagSettings  # noqa: E402


def _client(corpus_dir, *, auth=False):
    settings = RagSettings()
    settings.paths.processed_dir = str(corpus_dir)
    if auth:
        settings.auth.enabled = True
        settings.auth.api_keys = {"k": "tenant-a"}
    # corpus endpoints never build the pipeline; a null factory is fine.
    return TestClient(create_app(lambda: None, settings))


# ── facets ────────────────────────────────────────────────────────────────────

def test_corpus_facets(corpus_dir):
    c = _client(corpus_dir)
    f = c.get("/corpus/facets").json()
    assert f["n_documents"] == 4
    assert f["n_chunks"] == 4          # 2 + 1 + 1 + 0
    assert f["n_timeseries_docs"] == 1
    assert f["date_range"]["min"] == "2026-02-04"
    assert f["date_range"]["max"] == "2026-06-17"

    tiers = {d["value"]: d["count"] for d in f["tiers"]}
    assert tiers == {1: 3, 6: 1}
    publishers = {d["value"]: d["count"] for d in f["publishers"]}
    assert publishers["Federal Reserve"] == 3
    entity = {d["value"] for d in f["entity_tags"]}
    assert {"china", "semiconductors"} <= entity


# ── documents list + filters ──────────────────────────────────────────────────

def test_corpus_documents_list_and_sort(corpus_dir):
    c = _client(corpus_dir)
    body = c.get("/corpus/documents").json()
    assert body["total"] == 4
    # newest first; the dated docs precede the undated dfast_data
    dates = [d["release_date"] for d in body["documents"] if d["release_date"]]
    assert dates == sorted(dates, reverse=True)


def test_corpus_documents_filters(corpus_dir):
    c = _client(corpus_dir)

    by_type = c.get("/corpus/documents", params={"doc_type": "fomc_statement"}).json()
    assert by_type["total"] == 1 and by_type["documents"][0]["doc_id"] == "fomc_stmt"

    by_pub = c.get("/corpus/documents", params={"publisher": "Federal Reserve"}).json()
    assert by_pub["total"] == 3

    by_tier = c.get("/corpus/documents", params={"tier": 1}).json()
    assert by_tier["total"] == 3

    by_entity = c.get("/corpus/documents", params={"entity_tag": "china"}).json()
    assert by_entity["total"] == 1 and by_entity["documents"][0]["doc_id"] == "bis_rule"

    by_q = c.get("/corpus/documents", params={"q": "bis"}).json()
    assert by_q["total"] == 1 and by_q["documents"][0]["doc_id"] == "bis_rule"

    by_date = c.get("/corpus/documents",
                    params={"date_from": "2026-05-01", "date_to": "2026-06-30"}).json()
    assert {d["doc_id"] for d in by_date["documents"]} == {"bis_rule", "fomc_stmt"}


def test_corpus_documents_pagination(corpus_dir):
    c = _client(corpus_dir)
    page = c.get("/corpus/documents", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 4 and len(page["documents"]) == 2
    page2 = c.get("/corpus/documents", params={"limit": 2, "offset": 2}).json()
    assert len(page2["documents"]) == 2
    ids = {d["doc_id"] for d in page["documents"]} | {d["doc_id"] for d in page2["documents"]}
    assert len(ids) == 4


# ── document detail ───────────────────────────────────────────────────────────

def test_corpus_document_detail_and_404(corpus_dir):
    c = _client(corpus_dir)
    d = c.get("/corpus/documents/bis_rule").json()
    assert d["doc_id"] == "bis_rule" and d["n_chunks"] == 1
    assert d["chunks"][0]["text"].startswith("The Bureau of Industry")
    assert "china" in d["entity_tags"]

    dd = c.get("/corpus/documents/dfast_data").json()
    assert dd["timeseries"]["n_rows"] == 4
    assert dd["timeseries"]["sources"] == ["DFAST_2026"]
    assert dd["timeseries"]["variables"] == ["unemployment_rate"]

    assert c.get("/corpus/documents/nope").status_code == 404


# ── series catalog ────────────────────────────────────────────────────────────

def test_corpus_series_catalog(corpus_dir):
    c = _client(corpus_dir)
    s = c.get("/corpus/series").json()
    assert s["n_rows_total"] == 4
    src = {e["source"]: e for e in s["sources"]}
    assert "DFAST_2026" in src
    assert src["DFAST_2026"]["n_rows"] == 4
    assert src["DFAST_2026"]["variables"] == ["unemployment_rate"]
    assert "Federal Reserve" in src["DFAST_2026"]["publishers"]


# ── auth gate ─────────────────────────────────────────────────────────────────

def test_corpus_respects_auth(corpus_dir):
    c = _client(corpus_dir, auth=True)
    assert c.get("/corpus/facets").status_code == 401
    assert c.get("/corpus/facets", headers={"X-API-Key": "k"}).status_code == 200
