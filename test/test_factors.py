"""Factor-discovery: controlled-vocab validation, ranking, the citation gate,
and the discoverer round-trip (mirrors test_synthesis.py)."""

from __future__ import annotations

import json

from factors.discoverer import discover_factors
from factors.gate import gate_factors
from factors.schema import RelevantFactor
from factors.vocab import factor_reference, valid_entity_tags, valid_factor_tags
from llm.fake import FakeLLM
from utils.schema import RetrievedChunk


def _chunk(cid, rel="2026-06-10"):
    return RetrievedChunk(cid, f"text {cid}", 1.0,
                          {"release_date": rel, "publisher": "X", "publication": "Y"})


# ── vocabulary ────────────────────────────────────────────────────────────────

def test_factor_reference_lists_controlled_tags():
    ref = factor_reference()
    assert "export_controls" in ref
    assert "export_controls" in valid_factor_tags()
    assert "china" in valid_entity_tags()


# ── discoverer validation + ranking ───────────────────────────────────────────

def test_discoverer_keeps_vocab_drops_unknown_and_ranks():
    chunks = [_chunk("c1")]
    payload = {"factors": [
        {"factor_tag": "china_taiwan_tail", "relevance": "medium", "chunk_ids": ["c1"]},
        {"factor_tag": "export_controls", "relevance": "high",
         "entity_tags": ["china", "bogus_entity"], "chunk_ids": ["c1"]},
        {"factor_tag": "not_a_tag", "relevance": "high", "chunk_ids": ["c1"]},   # off-vocab
    ]}
    out = discover_factors(FakeLLM(responses=[json.dumps(payload)]), "ai portfolio",
                           chunks)
    tags = [f.factor_tag for f in out]
    assert tags == ["export_controls", "china_taiwan_tail"]    # high before medium
    assert out[0].entity_tags == ["china"]                     # off-vocab entity dropped


def test_discoverer_top_k_limits():
    chunks = [_chunk("c1")]
    payload = {"factors": [
        {"factor_tag": "export_controls", "relevance": "high", "chunk_ids": ["c1"]},
        {"factor_tag": "china_taiwan_tail", "relevance": "low", "chunk_ids": ["c1"]},
    ]}
    out = discover_factors(FakeLLM(responses=[json.dumps(payload)]), "q", chunks,
                           top_k=1)
    assert [f.factor_tag for f in out] == ["export_controls"]


def test_discoverer_empty_when_no_chunks():
    assert discover_factors(FakeLLM(responses=['{"factors":[]}']), "q", []) == []


# ── citation gate ──────────────────────────────────────────────────────────────

def test_gate_drops_factor_without_present_dated_cite():
    chunks = [_chunk("c1", "2026-06-10"), _chunk("c2", None)]    # c2 has no date
    factors = [
        RelevantFactor("export_controls", "high", chunk_ids=["c1"]),
        RelevantFactor("china_taiwan_tail", "high", chunk_ids=["c2"]),   # undated
        RelevantFactor("discount_rate", "low", chunk_ids=["nope"]),      # absent
    ]
    kept = gate_factors(factors, chunks)
    assert [f.factor_tag for f in kept] == ["export_controls"]
    assert kept[0].chunk_ids == ["c1"]
