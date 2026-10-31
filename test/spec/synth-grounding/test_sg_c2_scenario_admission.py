"""C2: heuristic-only entity tags do not shut out scenario studies in synthesize."""

from __future__ import annotations

import json
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from utils.schema import MetaFilter

# Whole-word cues in this query (C1 rule + the documented cue table):
#   factor: "geopolitical" -> geopolitical_risk, "equity vol" -> equity_vol
#   entity: "semiconductor" -> semiconductors
# No year, so no date window. No other cue occurs even as a substring.
QUERY = "geopolitical shock to equity vol from a semiconductor export blockade"
HEURISTIC_ENTITIES = {"semiconductors"}
HEURISTIC_FACTORS = {"geopolitical_risk", "equity_vol"}


def _responder(system, user):
    if "SHOCK VECTOR" in user:
        return json.dumps({"shock_vector": {}})
    return json.dumps({"summary": "scenario narrative", "details": [],
                       "unanswered": []})


def _ids(res):
    return {c.chunk_id for c in res.query.chunks}


@pytest.mark.criterion("C2")
def test_heuristic_entity_facet_admits_scenario_studies_only(tmp_path, make_pipeline,
                                                             sg):
    # oracle: the spec's four-document corpus, by inspection of the C2 rule.
    # Heuristic facets: factor {geopolitical_risk, equity_vol}, entity {semiconductors}
    # (no caller entity_tags). Every doc intersects the factor facet.
    #   EBA    entity []                 scenario tags -> admitted (rule)
    #   GFSR   entity [china]            scenario tag  -> admitted (rule)
    #   TAIWAN entity [china, semis]     entity match  -> admitted
    #   DISTRACTOR entity [] no scenario tag           -> excluded by entity facet
    pipe, _ = make_pipeline(tmp_path, _responder)
    res = pipe.synthesize(QUERY)
    assert set(res.query.analysis.filters.entity_tags) == HEURISTIC_ENTITIES
    assert set(res.query.analysis.filters.factor_tags) == HEURISTIC_FACTORS
    assert _ids(res) == {sg.chunk_id(sg.EBA), sg.chunk_id(sg.GFSR),
                         sg.chunk_id(sg.TAIWAN)}


@pytest.mark.criterion("C2")
def test_other_facets_still_apply_to_scenario_studies(tmp_path, make_pipeline, sg):
    # oracle: C2 rule by inspection. A caller publisher facet ["EBA"] with no caller
    # entity_tags: entity tags are still heuristic-only, so the EBA study (publisher
    # EBA, scenario-tagged) is admitted; GFSR fails the publisher facet; TAIWAN and
    # DISTRACTOR fail the publisher facet too. Expected {EBA} exactly.
    pipe, _ = make_pipeline(tmp_path, _responder)
    res = pipe.synthesize(QUERY, base_filter=MetaFilter(publishers=["EBA"]))
    assert _ids(res) == {sg.chunk_id(sg.EBA)}

    # oracle: same rule; a date window ending 2025-02-28 admits only docs released
    # on/before it: EBA (2025-01-31) passes, GFSR (2025-04-22), TAIWAN (2025-06-01)
    # and DISTRACTOR (2025-03-03) fail the date facet. Expected {EBA} exactly.
    res = pipe.synthesize(QUERY, base_filter=MetaFilter(date_to="2025-02-28"))
    assert _ids(res) == {sg.chunk_id(sg.EBA)}


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_caller_entity_tags_stay_a_hard_filter(tmp_path, make_pipeline, sg):
    # oracle: metamorphic relation from the C2 rule. Supplying the heuristic entity
    # set explicitly as caller entity_tags turns the entity facet hard for every
    # chunk: the scenario studies (EBA entity [], GFSR entity [china]) drop out and
    # nothing else changes. So heuristic-only result minus caller result is exactly
    # {EBA, GFSR}, and the caller result is {TAIWAN}.
    pipe, _ = make_pipeline(tmp_path, _responder)
    heuristic = _ids(pipe.synthesize(QUERY))
    caller = _ids(pipe.synthesize(
        QUERY, base_filter=MetaFilter(entity_tags=sorted(HEURISTIC_ENTITIES))))
    assert caller == {sg.chunk_id(sg.TAIWAN)}
    assert heuristic - caller == {sg.chunk_id(sg.EBA), sg.chunk_id(sg.GFSR)}


_ENTITY_VOCAB = ["ai", "china", "data_center", "hyperscaler", "semiconductors",
                 "quantum"]


@pytest.mark.criterion("C2")
@settings(max_examples=40)
@given(entities=st.lists(st.sampled_from(_ENTITY_VOCAB), unique=True, max_size=3),
       scenario=st.sets(st.sampled_from(["cross_asset_scenario", "stress_scenario"]),
                        max_size=2),
       caller_entities=st.booleans())
def test_probe_admission_follows_rule(make_pipeline, sg, entities, scenario,
                                      caller_entities):
    # oracle: the C2 rule as a predicate, applied per generated probe document.
    # The probe always passes the factor facet (carries equity_vol) and has no
    # other restrictive facet. The TAIWAN paper is always present and always
    # admitted, so filtered retrieval is never empty.
    #   heuristic-only entity facet: admitted iff entity match OR scenario tag;
    #   caller entity_tags={semiconductors}: admitted iff entity match.
    probe = sg.make_doc("probe", factor_tags=["equity_vol", *sorted(scenario)],
                        entity_tags=entities, tier=7, publisher="Probe",
                        doc_type="probe_note", release_date="2025-05-05",
                        text=QUERY + ": probe study")
    taiwan = [d for d in sg.scenario_corpus() if d["doc_id"] == sg.TAIWAN]
    entity_match = bool(set(entities) & HEURISTIC_ENTITIES)
    if caller_entities:
        expected = entity_match
        base = MetaFilter(entity_tags=sorted(HEURISTIC_ENTITIES))
    else:
        expected = entity_match or bool(scenario)
        base = None
    with tempfile.TemporaryDirectory() as d:
        pipe, _ = make_pipeline(d, _responder, docs=[*taiwan, probe])
        ids = _ids(pipe.synthesize(QUERY, base_filter=base))
    assert sg.chunk_id(sg.TAIWAN) in ids
    assert (sg.chunk_id("probe") in ids) is expected
