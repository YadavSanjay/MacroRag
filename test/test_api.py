"""FastAPI service tests (fake pipeline; no models/network)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from llm.fake import FakeLLM  # noqa: E402
from pipelines.query_pipeline import QueryPipeline  # noqa: E402
from retriever.rerank import IdentityReranker  # noqa: E402
from retriever.retriever import Retriever  # noqa: E402
from utils.cache import NullCache  # noqa: E402
from utils.config import RagSettings, RetrievalSettings  # noqa: E402
from utils.manifest import build_manifest, write_manifest  # noqa: E402
from utils.trace import Tracer  # noqa: E402

_ANSWER = ('{"summary":"Unemployment peaks at 10%.",'
           '"details":[{"text":"peak 10%","chunk_ids":["dfast_scen::000"]}],'
           '"unanswered":[]}')


@pytest.fixture
def client(indexed, tmp_path):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    settings = RagSettings()
    settings.paths.feedback_log = str(tmp_path / "fb.jsonl")
    settings.retrieval.verify_faithfulness = False
    pipe = QueryPipeline(retriever, FakeLLM(responder=lambda s, u: _ANSWER),
                         None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache())
    app = create_app(lambda: pipe, settings)
    return TestClient(app), tmp_path


def test_healthz(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_query_returns_cited_answer(client):
    c, _ = client
    r = c.post("/query", json={"question": "severely adverse unemployment stress test"})
    assert r.status_code == 200
    body = r.json()
    assert "10%" in body["summary"]
    assert body["route"] == "DIRECT" and body["cached"] is False
    assert any(ch["source_id"] == "dfast" for ch in body["chunks"])


def test_query_accepts_filters(client):
    c, _ = client
    r = c.post("/query", json={"question": "export controls on chips",
                               "filters": {"entity_tags": ["china"]}})
    assert r.status_code == 200
    assert any(ch["source_id"] == "federal_register" for ch in r.json()["chunks"])


def test_citations_lookup_and_404(client):
    c, _ = client
    cid = c.post("/query", json={"question": "severely adverse unemployment"}
                 ).json()["chunks"][0]["chunk_id"]
    r = c.get(f"/citations/{cid}")
    assert r.status_code == 200 and r.json()["chunk_id"] == cid and r.json()["text"]
    assert c.get("/citations/nope").status_code == 404


def test_citations_batch_resolves_dedupes_and_reports_missing(client):
    c, _ = client
    cid = c.post("/query", json={"question": "severely adverse unemployment"}
                 ).json()["chunks"][0]["chunk_id"]
    r = c.post("/citations", json={"chunk_ids": [cid, cid, "missing::999"]})
    assert r.status_code == 200
    body = r.json()
    assert [x["chunk_id"] for x in body["citations"]] == [cid]   # deduped, order kept
    assert body["citations"][0]["text"]
    assert body["missing"] == ["missing::999"]


def test_citations_batch_empty_is_ok(client):
    c, _ = client
    r = c.post("/citations", json={"chunk_ids": []})
    assert r.status_code == 200 and r.json() == {"citations": [], "missing": []}


def test_citations_batch_requires_auth_when_enabled(indexed, tmp_path):
    c = _auth_client(indexed, tmp_path)
    assert c.post("/citations", json={"chunk_ids": ["x"]}).status_code == 401


def test_feedback_appends_jsonl(client):
    c, tmp = client
    r = c.post("/feedback", json={"question": "q", "rating": -1, "note": "wrong"})
    assert r.status_code == 200
    rec = json.loads((tmp / "fb.jsonl").read_text(encoding="utf-8").strip())
    assert rec["rating"] == -1 and rec["question"] == "q"


def test_query_stream_emits_sse_events(client):
    c, _ = client
    r = c.post("/query/stream", json={"question": "unemployment"})
    assert r.status_code == 200
    assert "event: retrieved" in r.text
    assert "event: answer" in r.text and "event: done" in r.text


# ── P2: auth + rate limiting ──────────────────────────────────────────────────

def _auth_client(indexed, tmp_path, rpm=60):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=2))
    settings = RagSettings()
    settings.paths.feedback_log = str(tmp_path / "fb.jsonl")
    settings.retrieval.verify_faithfulness = False
    settings.auth.enabled = True
    settings.auth.api_keys = {"good-key": "tenant-a"}
    settings.auth.rate_limit_rpm = rpm
    pipe = QueryPipeline(retriever, FakeLLM(responder=lambda s, u: _ANSWER),
                         None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache())
    return TestClient(create_app(lambda: pipe, settings))


def test_auth_required_and_enforced(indexed, tmp_path):
    c = _auth_client(indexed, tmp_path)
    assert c.post("/query", json={"question": "x"}).status_code == 401          # no key
    assert c.post("/query", json={"question": "x"},
                  headers={"X-API-Key": "bad"}).status_code == 401              # wrong key
    ok = c.post("/query", json={"question": "severely adverse unemployment"},
                headers={"X-API-Key": "good-key"})
    assert ok.status_code == 200


def test_rate_limit_returns_429(indexed, tmp_path):
    c = _auth_client(indexed, tmp_path, rpm=1)
    h = {"X-API-Key": "good-key"}
    first = c.post("/query", json={"question": "unemployment"}, headers=h)
    second = c.post("/query", json={"question": "unemployment"}, headers=h)
    assert first.status_code == 200 and second.status_code == 429


# ── scenario-synthesis (AITrade contract, spec §8 acceptance) ─────────────────

_SYNTH = json.dumps({"shock_vector": {
    "USDTWD": {"value": 8, "unit": "pct", "chunk_ids": ["bis_rule::000"], "rationale": "BIS rule"},
    "IG_OAS": {"value": 90, "unit": "bp", "chunk_ids": ["bis_rule::000"]},
    "VIX": {"value": 18, "unit": "pts", "chunk_ids": ["bis_rule::000"]},
    "AI_EQUITY": {"value": -10, "unit": "pct", "chunk_ids": ["bis_rule::000"]},  # ignored field
}})


def _synth_client(indexed, tmp_path, capture=None):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=4))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False

    def responder(system, user):
        if "SHOCK VECTOR" in user:
            if capture is not None:
                capture.append(user)          # let tests inspect the synth prompt
            return _SYNTH
        return ('{"summary":"export-control escalation; TWD depreciates, IG widens",'
                '"details":[],"unanswered":[]}')

    pipe = QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache())
    return TestClient(create_app(lambda: pipe, settings))


def test_synthesize_mode_returns_gated_shock_vector(indexed, tmp_path):
    c = _synth_client(indexed, tmp_path)
    r = c.post("/query", json={
        "question": ("quantify the 10-day shocks to USD/TWD, IG credit spreads, "
                     "and VIX from an export-control escalation, with citations"),
        "filters": {"factor_tags": ["fx", "credit_spread", "equity_vol"],
                    "entity_tags": ["taiwan", "china", "TSMC"]},
        "mode": "synthesize"})
    assert r.status_code == 200
    body = r.json()
    sv = body["shock_vector"]
    # valid fields survive; unknown AI_EQUITY ignored
    assert set(sv) == {"USDTWD", "IG_OAS", "VIX"}
    assert sv["USDTWD"] == {"value": 8.0, "unit": "pct", "chunk_ids": ["bis_rule::000"],
                            "grounding_mode": "direct", "rationale": "BIS rule"}
    # GATE: every cited chunk is present in chunks[] with a release_date
    cited = {cid for f in sv.values() for cid in f["chunk_ids"]}
    dated = {ch["chunk_id"] for ch in body["chunks"] if ch.get("release_date")}
    assert cited and cited.issubset(dated)
    assert body["summary"]                       # prose rationale retained
    assert body["synthesis_diagnostics"]["outcome"] in ("grounded", "partial")


def test_answer_mode_has_no_shock_vector(indexed, tmp_path):
    c = _synth_client(indexed, tmp_path)
    body = c.post("/query", json={"question": "unemployment"}).json()
    assert "shock_vector" not in body          # only on mode=synthesize


# ── structured synthesis controls (fields / horizon_days / scenario) ──────────

def test_synthesize_fields_restrict_emitted_vector(indexed, tmp_path):
    c = _synth_client(indexed, tmp_path)
    r = c.post("/query", json={
        "question": "quantify export-control escalation shocks",
        "mode": "synthesize",
        "synthesis": {"fields": ["IG_OAS", "VIX"]}})
    assert r.status_code == 200
    sv = r.json()["shock_vector"]
    # LLM returned USDTWD/IG_OAS/VIX, but the caller restricted to IG_OAS+VIX
    assert set(sv) == {"IG_OAS", "VIX"}


def test_synthesize_unknown_field_is_400(indexed, tmp_path):
    c = _synth_client(indexed, tmp_path)
    r = c.post("/query", json={
        "question": "x", "mode": "synthesize",
        "synthesis": {"fields": ["IG_OAS", "NOT_A_FIELD"]}})
    assert r.status_code == 400
    assert "NOT_A_FIELD" in r.json()["detail"]


def test_synthesize_horizon_and_scenario_thread_into_prompt(indexed, tmp_path):
    captured: list[str] = []
    c = _synth_client(indexed, tmp_path, capture=captured)
    r = c.post("/query", json={
        "question": "quantify the shocks",
        "mode": "synthesize",
        "synthesis": {"horizon_days": 10,
                      "scenario": "Taiwan cross-strait blockade"}})
    assert r.status_code == 200
    prompt = captured[0]
    assert "10 day(s)" in prompt                       # horizon constraint rendered
    assert "Taiwan cross-strait blockade" in prompt    # scenario folded into the query


# ── factor discovery (POST /factors) ──────────────────────────────────────────

_FACTORS = json.dumps({"factors": [
    {"factor_tag": "export_controls", "relevance": "high", "entity_tags": ["china"],
     "chunk_ids": ["bis_rule::000"], "rationale": "BIS export rule on AI chips"},
    {"factor_tag": "china_taiwan_tail", "relevance": "medium",
     "chunk_ids": ["bis_rule::000"]},
    {"factor_tag": "not_a_real_tag", "relevance": "high",        # dropped: off-vocab
     "chunk_ids": ["bis_rule::000"]},
    {"factor_tag": "discount_rate", "relevance": "low",          # dropped: ungated cite
     "chunk_ids": ["does_not_exist::999"]},
]})


def _factors_client(indexed, tmp_path):
    embedder, store, _ = indexed
    retriever = Retriever(embedder, store, IdentityReranker(),
                          settings=RetrievalSettings(final_k=4))
    settings = RagSettings()
    settings.retrieval.verify_faithfulness = False
    # world_view_id comes from the manifest: use a synthetic one, not data/'s
    settings.paths.manifest = str(tmp_path / "manifest.json")
    write_manifest(settings.manifest_path, build_manifest(settings, chunks=len(indexed[2])))

    def responder(system, user):
        if "PORTFOLIO:" in user:        # the discover_factors prompt
            return _FACTORS
        return '{"summary":"","details":[],"unanswered":[]}'

    pipe = QueryPipeline(retriever, FakeLLM(responder=responder), None, settings,
                         tracer=Tracer(tmp_path / "t.jsonl", enabled=False),
                         cache=NullCache())
    return TestClient(create_app(lambda: pipe, settings))


def test_factors_returns_ranked_gated_factors(indexed, tmp_path):
    c = _factors_client(indexed, tmp_path)
    r = c.post("/factors", json={
        "portfolio": "AI semiconductor names exposed to China export controls",
        "filters": {"entity_tags": ["china", "semiconductors"]}})
    assert r.status_code == 200
    body = r.json()
    tags = [f["factor_tag"] for f in body["factors"]]
    # off-vocab and ungated factors dropped; valid ones ranked high → medium
    assert tags == ["export_controls", "china_taiwan_tail"]
    assert body["factors"][0]["relevance"] == "high"
    assert body["factors"][0]["entity_tags"] == ["china"]
    # GATE: every cited chunk is present in chunks[] with a release_date
    cited = {cid for f in body["factors"] for cid in f["chunk_ids"]}
    dated = {ch["chunk_id"] for ch in body["chunks"] if ch.get("release_date")}
    assert cited and cited.issubset(dated)
    assert body.get("world_view_id")


def test_factors_respects_top_k(indexed, tmp_path):
    c = _factors_client(indexed, tmp_path)
    r = c.post("/factors", json={"portfolio": "AI chips", "top_k": 1})
    assert r.status_code == 200
    assert len(r.json()["factors"]) == 1          # highest-ranked only


def test_factors_requires_auth_when_enabled(indexed, tmp_path):
    c = _auth_client(indexed, tmp_path)
    assert c.post("/factors", json={"portfolio": "x"}).status_code == 401


# ── discovery: public vocabulary + shock-field schema ─────────────────────────

def test_synthesis_schema_lists_shock_fields(client):
    c, _ = client
    r = c.get("/synthesis/schema")
    assert r.status_code == 200
    fields = r.json()["fields"]
    by_name = {f["name"]: f for f in fields}
    # every contract field present with units + sign convention
    assert {"UST_10Y", "IG_OAS", "VIX", "USDTWD"} <= set(by_name)
    assert by_name["IG_OAS"]["units"] == ["bp"]
    assert by_name["IG_OAS"]["preferred_unit"] == "bp"
    assert by_name["USDTWD"]["positive_means"]


def test_vocabulary_exposes_controlled_tags(client):
    c, _ = client
    r = c.get("/vocabulary")
    assert r.status_code == 200
    body = r.json()
    assert {"factor_tags", "entity_tags", "doc_types", "aliases"} <= set(body)
    assert isinstance(body["factor_tags"], list)
    assert body["factor_tags"] == sorted(body["factor_tags"])    # sorted for stable consumption


def test_discovery_requires_auth_when_enabled(indexed, tmp_path):
    c = _auth_client(indexed, tmp_path)
    assert c.get("/synthesis/schema").status_code == 401         # no key
    assert c.get("/vocabulary").status_code == 401
    ok = c.get("/synthesis/schema", headers={"X-API-Key": "good-key"})
    assert ok.status_code == 200
