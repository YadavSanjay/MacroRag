"""C1: unit_scale_mismatch flags a value that copies a cited number's digits but not its unit."""

from __future__ import annotations

import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import check_analog_quality

# Conversion factors from the spec's Definitions table (quantity kind -> field unit).
# A missing pair is undefined.
FACTOR = {
    ("percent", "bp"): 100.0, ("pp", "bp"): 100.0, ("bp", "bp"): 1.0,
    ("percent", "pct"): 1.0, ("pp", "pct"): 1.0, ("bp", "pct"): 0.01,
    ("points", "pts"): 1.0,
    ("percent", "rel"): 0.01, ("pp", "rel"): 0.01,
}
TOKENS = {
    "percent": ["percent", "per cent", "%"],
    "pp": ["percentage points", "percentage point", "pp"],
    "bp": ["basis points", "basis point", "bps", "bp"],
    "points": ["index points", "points", "pts"],
}
# (field, unit) pairs valid in synthesis/vocab.py, one per field unit.
FIELD_UNITS = [("IG_OAS", "bp"), ("EQUITY_US", "pct"), ("VIX", "pts"), ("MOVE", "rel")]

CTRL_CID = "ctrl_note::000"


def _control(saq):
    """A field that flags by the spec's first oracle example (1 bp vs 1 percent):
    it makes every test fail against a do-nothing implementation."""
    return (saq.sf("HY_OAS", 1.0, "bp", [CTRL_CID]),
            saq.chunk(CTRL_CID, "high-yield spreads rose more than 1 percent"))


def _run(saq, field, text, *, extra_chunks=()):
    cid = field.chunk_ids[0] if field.chunk_ids else "none::000"
    ctrl, ctrl_chunk = _control(saq)
    fields = {field.name: field, "HY_OAS": ctrl}
    chunks = [ctrl_chunk, *extra_chunks]
    if text is not None:
        chunks.append(saq.chunk(cid, text))
    diag = SynthesisDiagnostics()
    check_analog_quality(fields, chunks, diag=diag)
    assert set(ctrl.validation) == {saq.CODE_UNIT}
    assert len(saq.warns(diag, saq.CODE_UNIT, "HY_OAS")) == 1
    return diag


def _assert_flag(saq, diag, f, flagged, cited=None, cited_unit=None):
    w = saq.warns(diag, saq.CODE_UNIT, f.name)
    if not flagged:
        assert saq.CODE_UNIT not in f.validation
        assert w == []
        return
    assert set(f.validation) == {saq.CODE_UNIT}
    assert len(w) == 1
    # tolerance: values pass through float parsing only; 1e-9 is far below any digit used.
    assert w[0]["value"] == pytest.approx(f.value, rel=1e-9)
    assert w[0]["unit"] == f.unit
    assert abs(float(w[0]["cited"])) == pytest.approx(cited, rel=1e-9)
    assert w[0]["cited_unit"] == cited_unit


@pytest.mark.criterion("C1")
def test_spec_oracle_examples(saq):
    # oracle: the spec's C1 oracle list, worked by hand with the Definitions tables.
    # IG_OAS 1 bp vs "more than 1 percent": |1| = |1|, percent->bp = 100 != 1 -> (a);
    #   only quantity 1*100 = 100, far from 1 -> (b) -> flag (direct and analog alike).
    # IG_OAS 100 bp, same text: (a) needs |q| = 100, none -> no flag.
    # EQUITY_US -10 pct vs "10 percent": percent->pct = 1, so (a) fails -> no flag.
    # VIX 10 pts vs "10 percent": percent->pts undefined -> ignored -> no flag.
    text = "CDS spreads generally increase more than 1 percent cumulatively"
    for mode in ("direct", "analog"):
        f = saq.sf("IG_OAS", 1.0, "bp", ["g::000"], mode=mode)
        _assert_flag(saq, _run(saq, f, text), f, True, cited=1.0, cited_unit="percent")
    f = saq.sf("IG_OAS", 100.0, "bp", ["g::000"])
    _assert_flag(saq, _run(saq, f, text), f, False)
    f = saq.sf("EQUITY_US", -10.0, "pct", ["e::000"])
    _assert_flag(saq, _run(saq, f, "equities fell 10 percent"), f, False)
    f = saq.sf("VIX", 10.0, "pts", ["v::000"])
    _assert_flag(saq, _run(saq, f, "implied vol rose 10 percent"), f, False)


@pytest.mark.criterion("C1")
def test_unit_token_variants(saq):
    # oracle: Definitions applied by hand per case (token -> kind -> factor to unit).
    cases = [
        # (field, value, unit, text, flagged, cited, cited_unit)
        ("IG_OAS", 25.0, "bp", "spreads widened 25 pp", True, 25.0, "pp"),        # pp->bp 100
        ("IG_OAS", 2.0, "bp", "spreads rose 2 per cent", True, 2.0, "percent"),   # per cent
        ("IG_OAS", 3.0, "bp", "spreads widened 3%", True, 3.0, "percent"),        # % no space
        ("USDJPY", 50.0, "pct", "the move was 50 bps", True, 50.0, "bp"),         # bp->pct 0.01
        ("MOVE", 20.0, "rel", "rates vol jumped 20 percent", True, 20.0, "percent"),  # ->rel 0.01
        ("IG_OAS", 50.0, "bp", "spreads widened 50 bps", False, None, None),      # bp->bp = 1
        ("VIX", 8.0, "pts", "VIX rose 8 points", False, None, None),              # points->pts 1
        ("IG_OAS", 1.0, "bp", "the 1 percentile of the distribution", False, None, None),
        # ^ "percent" inside "percentile" does not end at a word boundary: no quantity.
    ]
    for name, value, unit, text, flagged, cited, kind in cases:
        f = saq.sf(name, value, unit, [f"{name.lower()}::000"])
        _assert_flag(saq, _run(saq, f, text), f, flagged, cited=cited, cited_unit=kind)


@pytest.mark.criterion("C1")
def test_masking_and_citation_scope(saq):
    # oracle: Definitions applied by hand.
    # (1) "1 percent ... 1 bp": (a) holds via 1 percent, but 1 bp * 1 = 1 matches the
    #     value within 5% -> (b) fails -> no flag.
    # (2) the "1 percent" sits in an uncited chunk; cited text has no quantity -> no flag.
    # (3) the only cited id does not resolve -> cited text is empty -> no flag.
    # (4) value -1 bp vs "-1 percent": magnitudes compared, |-1| = |-1| -> flag.
    f = saq.sf("IG_OAS", 1.0, "bp", ["m::000"])
    _assert_flag(saq, _run(saq, f, "spreads rose more than 1 percent, about 1 bp a day"),
                 f, False)
    f = saq.sf("IG_OAS", 1.0, "bp", ["cited::000"])
    _assert_flag(saq, _run(saq, f, "spreads widened modestly",
                           extra_chunks=[saq.chunk("other::000", "rose 1 percent")]),
                 f, False)
    f = saq.sf("IG_OAS", 1.0, "bp", ["missing::000"])
    _assert_flag(saq, _run(saq, f, None,
                           extra_chunks=[saq.chunk("other::000", "rose 1 percent")]),
                 f, False)
    f = saq.sf("IG_OAS", -1.0, "bp", ["n::000"])
    _assert_flag(saq, _run(saq, f, "spreads moved -1 percent"), f, True,
                 cited=1.0, cited_unit="percent")


def _num(choice: str, v: int, other: int) -> tuple[str, float]:
    if choice == "same":
        return str(v), float(v)
    if choice == "div100":
        return f"{v / 100:g}", v / 100
    if choice == "mul100":
        return str(v * 100), float(v * 100)
    return str(other), float(other)


quantity = st.tuples(st.sampled_from(sorted(TOKENS)), st.integers(0, 3),
                     st.sampled_from(["same", "div100", "mul100", "other"]),
                     st.integers(1, 400), st.booleans())


@pytest.mark.criterion("C1")
@settings(max_examples=30)
@given(fu=st.sampled_from(FIELD_UNITS), v=st.integers(1, 400), neg=st.booleans(),
       qs=st.lists(quantity, min_size=1, max_size=2))
def test_flag_iff_a_and_b(saq, fu, v, neg, qs):
    # oracle: conditions (a) and (b) of C1 computed in the test from the spec's
    # Definitions tables (FACTOR, TOKENS above), over generated cited quantities.
    name, unit = fu
    value = -float(v) if neg else float(v)
    parts, parsed = [], []
    for kind, ti, choice, other, qneg in qs:
        token = TOKENS[kind][ti % len(TOKENS[kind])]
        s, q = _num(choice, v, other)
        sp = "" if token == "%" else " "
        parts.append(f"moved {'-' if qneg else ''}{s}{sp}{token}")
        parsed.append((kind, q))
    text = "the series " + " and ".join(parts) + " over the episode"
    av = abs(value)
    defined = [(k, q, FACTOR[(k, unit)]) for k, q in parsed if (k, unit) in FACTOR]
    for _, q, fac in defined:   # keep clear of the 5% boundary of (b)
        assume(abs(abs(q * fac - av) - 0.05 * av) > 1e-6 * av)
    a_kinds = [k for k, q, fac in defined
               if math.isclose(q, av, rel_tol=1e-6) and not math.isclose(fac, 1.0)]
    b_masked = any(abs(q * fac - av) <= 0.05 * av for _, q, fac in defined)
    flagged = bool(a_kinds) and not b_masked

    f = saq.sf(name, value, unit, ["gen::000"])
    diag = _run(saq, f, text)
    w = saq.warns(diag, saq.CODE_UNIT, name)
    if flagged:
        assert set(f.validation) == {saq.CODE_UNIT}
        assert len(w) == 1
        assert w[0]["unit"] == unit
        assert w[0]["cited_unit"] in a_kinds
        # tolerance: the cited number is parsed from its decimal text; 1e-6 is the spec's.
        assert abs(float(w[0]["cited"])) == pytest.approx(av, rel=1e-6)
    else:
        assert saq.CODE_UNIT not in f.validation
        assert w == []
