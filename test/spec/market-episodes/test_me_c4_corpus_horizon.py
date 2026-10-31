"""C4: utils.corpus carries metadata.horizon_days into the payload; the horizon check rates it."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.schema import ShockField
from synthesis.validate import check_source_horizon
from utils.corpus import iter_chunk_points
from utils.objectstore import InMemoryObjectStore
from utils.schema import RetrievedChunk

TOL = 3.0
CODES = ("horizon_mismatch", "horizon_unknown")


def _meta(doc_type: str, **extra) -> dict:
    meta = {"publisher": "MacroRAG (derived from FRED)", "publication": "P",
            "release_date": "2023-03-22", "retrieval_date": "2026-10-14",
            "url": "episode://svb", "content_hash": "h", "licence": "derived_fred",
            "tier": 3, "bucket": "C", "doc_type": doc_type, "section": None,
            "entity_tags": [], "factor_tags": ["cross_asset_scenario"],
            "trades_affected": []}
    meta.update(extra)
    return meta


def _doc(doc_id: str, doc_type: str, metas: list[dict]) -> dict:
    return {"doc_id": doc_id, "source_id": "s", "title": doc_id, "doc_type": doc_type,
            "publisher": "MacroRAG (derived from FRED)", "publication": "P",
            "release_date": "2023-03-22", "retrieval_date": "2026-10-14",
            "url": f"episode://{doc_id}", "content_hash": "h", "tier": 3, "bucket": "C",
            "chunks": [{"chunk_id": f"{doc_id}::{i:03d}", "seq": i,
                        "text": f"chunk {i} of {doc_id}", "metadata": m}
                       for i, m in enumerate(metas)],
            "timeseries": []}


def _payloads(source) -> dict[str, dict]:
    return {p.chunk_id: p.payload for p in iter_chunk_points(source)}


def _check(payload: dict, cid: str, h: int):
    """Run the horizon check for one IG_OAS field citing chunk ``cid``."""
    f = ShockField("IG_OAS", 120.0, "bp", chunk_ids=[cid], rationale="",
                   grounding_mode="analog", validation=[])
    diag = SynthesisDiagnostics()
    check_source_horizon({"IG_OAS": f}, [RetrievedChunk(cid, "t", 1.0, payload)],
                         horizon_days=h, tolerance=TOL, diag=diag)
    warns = [w for w in diag.validation if w.get("code") in CODES]
    return [c for c in f.validation if c in CODES], warns


@pytest.mark.criterion("C4")
def test_card_payload_carries_horizon_and_check_rates_it(tmp_path):
    # oracle: spec C4 + the synth-analog-horizon rule by hand. The card's chunks
    # carry horizon_days 5 and 10 -> payload["horizon_days"] 5 and 10; the ordinary
    # doc (no key) and a doc with horizon_days null get no key. A field citing the
    # 10-day chunk: H = 10 -> max(10/10, 10/10) = 1 <= 3 -> no code; H = 60 ->
    # max(10/60, 60/10) = 6 > 3 -> horizon_mismatch, basis "chunk", evidence [10].
    corpus = tmp_path / "processed" / "tierx"
    corpus.mkdir(parents=True)
    docs = [
        _doc("market_episode_svb", "market_episode",
             [_meta("market_episode", horizon_days=5),
              _meta("market_episode", horizon_days=10)]),
        _doc("note", "research_note", [_meta("research_note")]),
        _doc("nullh", "research_note", [_meta("research_note", horizon_days=None)]),
    ]
    for d in docs:
        (corpus / f"{d['doc_id']}.json").write_text(json.dumps(d), encoding="utf-8")
    pay = _payloads(tmp_path / "processed")

    assert pay["market_episode_svb::000"].get("horizon_days") == 5
    assert pay["market_episode_svb::001"].get("horizon_days") == 10
    assert "horizon_days" not in pay["note::000"]
    assert "horizon_days" not in pay["nullh::000"]

    cid = "market_episode_svb::001"
    codes, warns = _check(pay[cid], cid, 10)
    assert codes == [] and warns == []
    codes, warns = _check(pay[cid], cid, 60)
    assert codes == ["horizon_mismatch"]
    assert warns == [{"code": "horizon_mismatch", "field": "IG_OAS",
                      "requested_days": 60, "basis": "chunk", "evidence_days": [10]}]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(h=st.integers(min_value=1, max_value=400), req=st.integers(min_value=1, max_value=400))
def test_property_payload_only_gains_the_key_and_check_follows_ratio(h, req):
    # oracle: spec C4 (ordinary documents unchanged) + the synth-analog-horizon rule.
    # The payload of a chunk with horizon_days = h equals the payload of the same
    # chunk with horizon_days None, plus {"horizon_days": h}. A field citing it at
    # requested horizon req is flagged horizon_mismatch (basis chunk, evidence [h])
    # iff max(h/req, req/h) > 3, else gets no horizon code.
    store = InMemoryObjectStore()
    store.put_text("tierx/card.json", json.dumps(
        _doc("card", "market_episode", [_meta("market_episode", horizon_days=h)])))
    store.put_text("tierx/plain.json", json.dumps(
        _doc("plain", "market_episode", [_meta("market_episode", horizon_days=None)])))
    pay = _payloads(store)
    card, plain = pay["card::000"], pay["plain::000"]
    assert card.get("horizon_days") == h
    assert "horizon_days" not in plain
    strip = {k: v for k, v in card.items() if k != "horizon_days"}
    same = {k: v for k, v in plain.items() if k not in ("doc_id", "chunk_id", "title")}
    assert {k: v for k, v in strip.items() if k not in ("doc_id", "chunk_id", "title")} == same

    codes, warns = _check(card, "card::000", req)
    if max(h / req, req / h) > TOL:
        assert codes == ["horizon_mismatch"]
        assert warns == [{"code": "horizon_mismatch", "field": "IG_OAS",
                          "requested_days": req, "basis": "chunk", "evidence_days": [h]}]
    else:
        assert codes == [] and warns == []
