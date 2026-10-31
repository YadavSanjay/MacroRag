"""C1: check_source_horizon flags fields whose evidence covers a very different horizon."""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import check_source_horizon

T = 3.0
CTRL = "HY_OAS"
CTRL_CID = "ctrl_card::000"
CTRL_DAYS = 10000


def _control(sah):
    """A field citing an exact 10000-day chunk: for every H <= 400 and T <= 4 used
    here, max(10000/H, H/10000) >= 25 > T, so it always flags (basis chunk,
    evidence [10000]). It makes every test fail against a do-nothing check."""
    return (sah.sf(CTRL, 100.0, "bp", [CTRL_CID]),
            sah.chunk(CTRL_CID, doc_type="research_note", horizon=CTRL_DAYS))


def _run(sah, fields, chunks, *, h=10, t=T, control=True):
    """Run the check (with the control field when ``control``) and return diag."""
    fields = dict(fields)
    chunks = list(chunks)
    ctrl = None
    if control:
        ctrl, ctrl_chunk = _control(sah)
        fields[CTRL] = ctrl
        chunks.append(ctrl_chunk)
    diag = SynthesisDiagnostics()
    before = set(fields)
    check_source_horizon(fields, chunks, horizon_days=h, tolerance=t, diag=diag)
    assert set(fields) == before            # never drops
    if ctrl is not None:
        assert ctrl.validation == [sah.CODE_MISMATCH]
        assert sah.warns(diag, CTRL) == [{
            "code": sah.CODE_MISMATCH, "field": CTRL, "requested_days": h,
            "basis": "chunk", "evidence_days": [CTRL_DAYS]}]
    return diag


def _expect(sah, diag, f, code, *, h=10, basis=None, evidence=None):
    """``code`` None means: no horizon code and no horizon warning for ``f``."""
    w = sah.warns(diag, f.name)
    if code is None:
        assert not set(sah.CODES) & set(f.validation)
        assert w == []
        return
    assert f.validation.count(code) == 1
    assert not (set(sah.CODES) - {code}) & set(f.validation)
    assert w == [{"code": code, "field": f.name, "requested_days": h,
                  "basis": basis, "evidence_days": evidence}]


@pytest.mark.criterion("C1")
def test_spec_oracle_examples(sah):
    # oracle: the spec's C1 oracle list, worked by hand with H = 10, T = 3.
    # dfast_scenarios: m = 90, H*T = 30 < 90 -> incompatible -> mismatch, basis chunk, [90].
    # H = 30: H*T = 90 >= 90 (inclusive) -> compatible -> no flag.
    # exact-10 chunk beside dfast_scenarios: max(10/10, 10/10) = 1 <= 3 compatible -> no flag.
    # d = 30: max(3, 1/3) = 3 <= 3 -> no flag.  d = 31: 3.1 > 3 -> mismatch, basis model, [31].
    # d = 4: max(0.4, 2.5) = 2.5 -> no flag.  d = 3: 10/3 = 3.33 > 3 -> mismatch, [3].
    # no rating, no d -> horizon_unknown, basis none, [].
    dfast = sah.chunk("dfast::000", doc_type="dfast_scenarios")
    exact10 = sah.chunk("card::000", doc_type="market_episode", horizon=10)
    plain = sah.chunk("note::000", doc_type="research_note")

    f = sah.sf("IG_OAS", 440.0, "bp", ["dfast::000"])
    _expect(sah, _run(sah, {"IG_OAS": f}, [dfast]), f, sah.CODE_MISMATCH,
            basis="chunk", evidence=[90])

    f = sah.sf("IG_OAS", 440.0, "bp", ["dfast::000"])
    _expect(sah, _run(sah, {"IG_OAS": f}, [dfast], h=30), f, None, h=30)

    f = sah.sf("IG_OAS", 440.0, "bp", ["card::000", "dfast::000"])
    _expect(sah, _run(sah, {"IG_OAS": f}, [dfast, exact10]), f, None)

    for d, code, evidence in [(30, None, None), (31, sah.CODE_MISMATCH, [31]),
                              (4, None, None), (3, sah.CODE_MISMATCH, [3])]:
        f = sah.sf("UST_10Y", 15.0, "bp", ["note::000"], declared=d)
        _expect(sah, _run(sah, {"UST_10Y": f}, [plain]), f, code,
                basis="model", evidence=evidence)

    f = sah.sf("UST_10Y", 15.0, "bp", ["note::000"])
    _expect(sah, _run(sah, {"UST_10Y": f}, [plain]), f, sah.CODE_UNKNOWN,
            basis="none", evidence=[])


@pytest.mark.criterion("C1")
def test_minimum_table_boundaries(sah):
    # oracle: the spec's doc_type table with T = 3, compatible iff H*T >= m (inclusive).
    # 90-day types: H = 30 -> 90 >= 90 ok; H = 29 -> 87 < 90 flag.
    # eba_esrb_adverse_scenario (365): H = 122 -> 366 ok; H = 121 -> 363 < 365 flag;
    #   H = 30 -> 90 < 365 flag.
    cases = [("dfast_scenarios", 30, False), ("dfast_scenarios", 29, True),
             ("dfast_scenario_data", 30, False), ("dfast_scenario_data", 29, True),
             ("dfast_results", 30, False), ("dfast_results", 29, True),
             ("eba_esrb_adverse_scenario", 122, False),
             ("eba_esrb_adverse_scenario", 121, True),
             ("eba_esrb_adverse_scenario", 30, True)]
    for doc_type, h, flagged in cases:
        m = sah.MIN_DAYS[doc_type]
        f = sah.sf("UST_2Y", 50.0, "bp", ["s::000"])
        diag = _run(sah, {"UST_2Y": f}, [sah.chunk("s::000", doc_type=doc_type)], h=h)
        _expect(sah, diag, f, sah.CODE_MISMATCH if flagged else None, h=h,
                basis="chunk", evidence=[m])


@pytest.mark.criterion("C1")
def test_exact_rating_bounds_and_precedence(sah):
    # oracle: Definitions applied by hand, H = 10, T = 3.
    # exact h = 30 -> ratio 3 <= 3 ok; h = 31 -> 3.1 flag; h = 4 -> 2.5 ok; h = 3 -> 3.33 flag.
    # Exact beats the table: dfast_scenarios chunk with horizon_days = 10 -> exact 10, ok;
    #   with horizon_days = 200 -> exact 200, ratio 20 -> flag, evidence [200] (m unused).
    for h_exact, flagged in [(30, False), (31, True), (4, False), (3, True)]:
        f = sah.sf("UST_5Y", 20.0, "bp", ["e::000"])
        diag = _run(sah, {"UST_5Y": f}, [sah.chunk("e::000", horizon=h_exact)])
        _expect(sah, diag, f, sah.CODE_MISMATCH if flagged else None,
                basis="chunk", evidence=[h_exact])
    f = sah.sf("UST_5Y", 20.0, "bp", ["e::000"])
    diag = _run(sah, {"UST_5Y": f},
                [sah.chunk("e::000", doc_type="dfast_scenarios", horizon=10)])
    _expect(sah, diag, f, None)
    f = sah.sf("UST_5Y", 20.0, "bp", ["e::000"])
    diag = _run(sah, {"UST_5Y": f},
                [sah.chunk("e::000", doc_type="dfast_scenarios", horizon=200)])
    _expect(sah, diag, f, sah.CODE_MISMATCH, basis="chunk", evidence=[200])


@pytest.mark.criterion("C1")
def test_non_int_horizon_payload_is_not_exact(sah):
    # oracle: Definitions rule 1: exact only for an int > 0, bool excluded. So
    # horizon_days of True, 0, -10, "10" or 10.0 is not an exact rating.
    # On a dfast_scenarios chunk the table applies: m = 90 > 30 -> mismatch, [90].
    # On an unrated doc_type with no d -> horizon_unknown, [].
    for bad in (True, 0, -10, "10", 10.0):
        f = sah.sf("UST_5Y", 20.0, "bp", ["b::000"])
        diag = _run(sah, {"UST_5Y": f},
                    [sah.chunk("b::000", doc_type="dfast_scenarios", horizon=bad)])
        _expect(sah, diag, f, sah.CODE_MISMATCH, basis="chunk", evidence=[90])
        f = sah.sf("UST_5Y", 20.0, "bp", ["b::000"])
        diag = _run(sah, {"UST_5Y": f},
                    [sah.chunk("b::000", doc_type="research_note", horizon=bad)])
        _expect(sah, diag, f, sah.CODE_UNKNOWN, basis="none", evidence=[])


@pytest.mark.criterion("C1")
def test_rated_evidence_overrides_declared_and_unrated(sah):
    # oracle: C1 (a)-(c) applied by hand, H = 10, T = 3.
    # dfast chunk (m = 90, incompatible) + d = 10: (a) applies, d ignored -> mismatch, [90].
    # exact-10 chunk + d = 500: (a), compatible -> no flag (d ignored).
    # unrated chunk + exact-10 chunk: the rated one decides -> no flag.
    # Four rated chunks 90, 90, 200, 365 all incompatible -> evidence sorted distinct
    #   [90, 200, 365].
    # Only an unresolved id cited, no d -> unknown; with d = 10 -> (b), ratio 1 -> no flag.
    chunks = [sah.chunk("dfast::000", doc_type="dfast_scenarios"),
              sah.chunk("res::000", doc_type="dfast_results"),
              sah.chunk("eba::000", doc_type="eba_esrb_adverse_scenario"),
              sah.chunk("x200::000", horizon=200),
              sah.chunk("x10::000", horizon=10),
              sah.chunk("note::000", doc_type="research_note")]
    f = sah.sf("IG_OAS", 440.0, "bp", ["dfast::000"], declared=10)
    _expect(sah, _run(sah, {"IG_OAS": f}, chunks), f, sah.CODE_MISMATCH,
            basis="chunk", evidence=[90])
    f = sah.sf("IG_OAS", 40.0, "bp", ["x10::000"], declared=500)
    _expect(sah, _run(sah, {"IG_OAS": f}, chunks), f, None)
    f = sah.sf("IG_OAS", 40.0, "bp", ["note::000", "x10::000"])
    _expect(sah, _run(sah, {"IG_OAS": f}, chunks), f, None)
    f = sah.sf("IG_OAS", 40.0, "bp",
               ["dfast::000", "res::000", "x200::000", "eba::000", "note::000"])
    _expect(sah, _run(sah, {"IG_OAS": f}, chunks), f, sah.CODE_MISMATCH,
            basis="chunk", evidence=[90, 200, 365])
    f = sah.sf("IG_OAS", 40.0, "bp", ["missing::000"])
    _expect(sah, _run(sah, {"IG_OAS": f}, chunks), f, sah.CODE_UNKNOWN,
            basis="none", evidence=[])
    f = sah.sf("IG_OAS", 40.0, "bp", ["missing::000"], declared=10)
    _expect(sah, _run(sah, {"IG_OAS": f}, chunks), f, None)


@pytest.mark.criterion("C1")
def test_analog_fields_existing_codes_and_no_diag(sah):
    # oracle: C1 by hand. The rule applies to analog fields too (dfast at H = 10 ->
    # mismatch). Existing validation codes are kept and the new code appears once.
    # With diag=None the field is still annotated.
    f = sah.sf("IG_OAS", 440.0, "bp", ["dfast::000"], mode="analog",
               validation=["implausible_magnitude"])
    diag = _run(sah, {"IG_OAS": f}, [sah.chunk("dfast::000", doc_type="dfast_scenarios")])
    assert f.validation.count("implausible_magnitude") == 1
    assert f.validation.count(sah.CODE_MISMATCH) == 1
    assert f.grounding_mode == "analog"
    assert sah.warns(diag, "IG_OAS") == [{
        "code": sah.CODE_MISMATCH, "field": "IG_OAS", "requested_days": 10,
        "basis": "chunk", "evidence_days": [90]}]

    g = sah.sf("UST_10Y", 15.0, "bp", ["note::000"], mode="analog")
    fields = {"UST_10Y": g}
    check_source_horizon(fields, [sah.chunk("note::000", doc_type="research_note")],
                         horizon_days=10, tolerance=T, diag=None)
    assert set(fields) == {"UST_10Y"}
    assert g.validation == [sah.CODE_UNKNOWN]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_absent_or_nonpositive_horizon_changes_nothing(sah):
    # oracle: C1 "With H absent or <= 0 it changes nothing and records nothing".
    # Relation: the same fields that flag at H = 10 (dfast -> mismatch; unrated with
    # no d -> unknown) are left untouched and no warning is recorded at H in
    # {None, 0, -10}.
    def make():
        return ({"IG_OAS": sah.sf("IG_OAS", 440.0, "bp", ["dfast::000"],
                                  validation=["implausible_magnitude"]),
                 "UST_10Y": sah.sf("UST_10Y", 15.0, "bp", ["note::000"])},
                [sah.chunk("dfast::000", doc_type="dfast_scenarios"),
                 sah.chunk("note::000", doc_type="research_note")])

    fields, chunks = make()
    diag = _run(sah, fields, chunks, control=False)
    assert fields["IG_OAS"].validation == ["implausible_magnitude", sah.CODE_MISMATCH]
    assert fields["UST_10Y"].validation == [sah.CODE_UNKNOWN]
    assert len(diag.validation) == 2

    for h in (None, 0, -10):
        fields, chunks = make()
        diag = SynthesisDiagnostics()
        check_source_horizon(fields, chunks, horizon_days=h, tolerance=T, diag=diag)
        assert set(fields) == {"IG_OAS", "UST_10Y"}
        assert fields["IG_OAS"].validation == ["implausible_magnitude"]
        assert fields["UST_10Y"].validation == []
        assert diag.validation == []


# ── property: (a)/(b)/(c) computed in the test from the Definitions ──────────
TABLE = sorted(["dfast_scenarios", "dfast_scenario_data", "dfast_results",
                "eba_esrb_adverse_scenario"])
NOT_EXACT = [True, False, 0, -7, "15", 15.0]

citation = st.one_of(
    st.tuples(st.just("exact"), st.integers(1, 800)),
    st.tuples(st.just("table"), st.sampled_from(TABLE)),
    st.tuples(st.just("table_with_bad_h"),
              st.tuples(st.sampled_from(TABLE), st.sampled_from(NOT_EXACT))),
    st.tuples(st.just("unrated"), st.sampled_from(["research_note", "speech", None])),
    st.tuples(st.just("missing"), st.just(None)),
)


def _expected(cites, d, req, tol):
    """(code, basis, evidence) per C1, or None when not flagged."""
    exact = [v for kind, v in cites if kind == "exact"]
    mins = ([MIN[v] for kind, v in cites if kind == "table"]
            + [MIN[v[0]] for kind, v in cites if kind == "table_with_bad_h"])
    if exact or mins:
        ok = (any(max(h / req, req / h) <= tol for h in exact)
              or any(req * tol >= m for m in mins))
        return None if ok else ("horizon_mismatch", "chunk", sorted(set(exact + mins)))
    if isinstance(d, int) and not isinstance(d, bool) and d > 0:
        if max(d / req, req / d) > tol:
            return ("horizon_mismatch", "model", [d])
        return None
    return ("horizon_unknown", "none", [])


MIN = {"dfast_scenarios": 90, "dfast_scenario_data": 90, "dfast_results": 90,
       "eba_esrb_adverse_scenario": 365}


@pytest.mark.criterion("C1")
@settings(max_examples=60)
@given(req=st.integers(1, 400), tol=st.sampled_from([1.5, 2.0, 3.0, 4.0]),
       spec=st.lists(st.tuples(st.lists(citation, min_size=0, max_size=4),
                               st.one_of(st.none(), st.integers(-5, 800))),
                     min_size=1, max_size=3))
def test_decision_matches_definitions(sah, req, tol, spec):
    # oracle: C1 (a)/(b)/(c) and the Definitions (MIN table copied from the spec),
    # computed in `_expected` over generated citations and declared horizons.
    names = ["UST_2Y", "UST_5Y", "UST_10Y"]
    fields, chunks, expected = {}, [], {}
    for i, (cites, d) in enumerate(spec):
        cids = []
        for j, (kind, v) in enumerate(cites):
            cid = f"c{i}_{j}::000"
            cids.append(cid)
            if kind == "exact":
                chunks.append(sah.chunk(cid, doc_type="research_note", horizon=v))
            elif kind == "table":
                chunks.append(sah.chunk(cid, doc_type=v))
            elif kind == "table_with_bad_h":
                chunks.append(sah.chunk(cid, doc_type=v[0], horizon=v[1]))
            elif kind == "unrated":
                chunks.append(sah.chunk(cid, doc_type=v))
            # "missing": the id does not resolve
        # keep generated ratios clear of the inclusive boundaries (float division)
        for kind, v in cites:
            if kind == "exact":
                assume(abs(max(v / req, req / v) - tol) > 1e-9)
            elif kind in ("table", "table_with_bad_h"):
                m = MIN[v if kind == "table" else v[0]]
                assume(abs(req * tol - m) > 1e-9)
        if d is not None and d > 0:
            assume(abs(max(d / req, req / d) - tol) > 1e-9)
        name = names[i]
        fields[name] = sah.sf(name, 25.0, "bp", cids, declared=d)
        expected[name] = _expected(cites, d, req, tol)

    diag = _run(sah, fields, chunks, h=req, t=tol, control=False)
    for name, f in fields.items():
        exp = expected[name]
        if exp is None:
            _expect(sah, diag, f, None, h=req)
        else:
            code, basis, evidence = exp
            _expect(sah, diag, f, code, h=req, basis=basis, evidence=evidence)


@pytest.mark.criterion("C1")
@settings(max_examples=40)
@given(req=st.integers(1, 400), d=st.integers(1, 800))
def test_declared_horizon_property(sah, req, d):
    # oracle: C1 (b) by hand: with no rated chunk, flag iff max(d/H, H/d) > T. Each
    # example also carries the always-flagging control field, so a do-nothing
    # check fails.
    assume(abs(max(d / req, req / d) - T) > 1e-9)
    f = sah.sf("UST_10Y", 15.0, "bp", ["note::000"], declared=d)
    diag = _run(sah, {"UST_10Y": f}, [sah.chunk("note::000", doc_type="research_note")],
                h=req)
    flagged = max(d / req, req / d) > T
    _expect(sah, diag, f, sah.CODE_MISMATCH if flagged else None, h=req,
            basis="model", evidence=[d])
