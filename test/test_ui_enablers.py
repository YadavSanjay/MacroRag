"""§E backend enablers: timeseries data points, vocabulary edit, tiers freshness."""

from __future__ import annotations

import json

import pytest

from api.models import answer_to_dict, timeseries_hit_to_dict
from utils.schema import Answer, TimeseriesHit

# ── timeseries data points (unblocks SeriesChart) ────────────────────────────

def test_timeseries_hit_serializes_plottable_series():
    hit = TimeseriesHit(source="DFAST_2026", publisher="Fed", rows=[
        {"variable": "unemployment_rate", "scenario": "severely_adverse",
         "period": "2026-Q1", "value": 5.9},
        {"variable": "unemployment_rate", "scenario": "severely_adverse",
         "period": "2026-Q2", "value": 7.2},
        {"variable": "bbb_yield", "scenario": "severely_adverse",
         "period": "2026-Q1", "value": 6.5},
    ])
    d = timeseries_hit_to_dict(hit)
    assert d["source"] == "DFAST_2026" and d["n_rows"] == 3
    by_var = {s["variable"]: s for s in d["series"]}
    assert by_var["unemployment_rate"]["scenario"] == "severely_adverse"
    pts = by_var["unemployment_rate"]["points"]
    assert pts == [{"period": "2026-Q1", "value": 5.9}, {"period": "2026-Q2", "value": 7.2}]
    assert by_var["bbb_yield"]["n_points"] == 1


def test_answer_to_dict_includes_series():
    ans = Answer(summary="s", details=[], timeseries=[
        TimeseriesHit(source="X", publisher="P",
                      rows=[{"variable": "v", "value": 1.0, "period": "2026"}])])
    out = answer_to_dict(ans)
    assert out["timeseries"][0]["series"][0]["points"][0]["value"] == 1.0


# ── vocabulary edit (PUT persistence) ────────────────────────────────────────

def test_save_vocabulary_fields_persists_and_reloads(monkeypatch, tmp_path):
    import ingest.common.config as cfg
    vocab = tmp_path / "vocabulary.yaml"
    vocab.write_text("factor_tags: [a]\nentity_tags: [china]\naliases: {taiwan: china}\n"
                     "doc_types: {group1: [fomc_statement]}\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "VOCAB_PATH", vocab)
    cfg.load_vocabulary.cache_clear()

    updated = cfg.save_vocabulary_fields(factor_tags=["term_premium", "ai_capex"])
    assert updated["factor_tags"] == {"ai_capex", "term_premium"}
    assert updated["entity_tags"] == {"china"}          # untouched field preserved
    assert updated["doc_types"] == {"fomc_statement"}   # grouped doc_types preserved
    # persisted to disk
    on_disk = vocab.read_text(encoding="utf-8")
    assert "term_premium" in on_disk and "china" in on_disk
    cfg.load_vocabulary.cache_clear()


# ── corpus freshness (tiers view) ────────────────────────────────────────────

def test_corpus_freshness_aggregates_latest_per_tier(tmp_path):
    from api.admin import _corpus_freshness
    d = tmp_path / "t1"
    d.mkdir()
    (d / "a.json").write_text(json.dumps(
        {"tier": 1, "source_id": "dfast", "retrieval_date": "2026-06-01",
         "release_date": "2026-02-04"}), encoding="utf-8")
    (d / "b.json").write_text(json.dumps(
        {"tier": 1, "source_id": "fomc", "retrieval_date": "2026-06-19",
         "release_date": "2026-06-17"}), encoding="utf-8")
    (d / "c.json").write_text(json.dumps(
        {"tier": 6, "source_id": "eia", "retrieval_date": "2026-05-01"}), encoding="utf-8")
    fresh = _corpus_freshness(tmp_path)
    assert fresh[1]["n_docs"] == 2
    assert fresh[1]["last_retrieval_date"] == "2026-06-19"   # max across tier-1 docs
    assert fresh[1]["last_release_date"] == "2026-06-17"
    assert isinstance(fresh[1]["days_since_ingest"], int)
    assert fresh[6]["n_docs"] == 1


def test_corpus_freshness_missing_dir_is_empty(tmp_path):
    from api.admin import _corpus_freshness
    assert _corpus_freshness(tmp_path / "nope") == {}


# ── PUT /admin/vocabulary endpoint ───────────────────────────────────────────

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from utils.config import RagSettings  # noqa: E402


def test_put_vocabulary_endpoint(monkeypatch, tmp_path):
    import ingest.common.config as cfg
    vocab = tmp_path / "vocabulary.yaml"
    vocab.write_text("factor_tags: [old]\nentity_tags: []\naliases: {}\ndoc_types: {}\n",
                     encoding="utf-8")
    monkeypatch.setattr(cfg, "VOCAB_PATH", vocab)
    cfg.load_vocabulary.cache_clear()
    c = TestClient(create_app(lambda: object(), RagSettings()))
    r = c.put("/admin/vocabulary", json={"factor_tags": ["new_tag"]})
    assert r.status_code == 200
    assert r.json()["factor_tags"] == ["new_tag"]
    assert "new_tag" in vocab.read_text(encoding="utf-8")
    cfg.load_vocabulary.cache_clear()
