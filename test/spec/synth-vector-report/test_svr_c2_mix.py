"""C2: check_episode_mix flags a vector whose fields cite more than one episode card."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthesis.diagnostics import SynthesisDiagnostics
from synthesis.validate import HARD_VALIDATION_CODES, check_episode_mix


@pytest.mark.criterion("C2")
def test_taiwan_shape_two_episodes_and_prose(svr):
    # oracle: the Definitions by hand. UST_10Y and USDJPY cite the tariff card, IG_OAS
    # cites the DeepSeek card, HY_OAS cites a research note: two episodes, so the three
    # card-citing fields gain episode_mixed and HY_OAS does not; one warning maps each
    # episode to its sorted fields. The code is soft.
    fields = {"UST_10Y": svr.sf("UST_10Y", -20.0, "bp", [svr.TARIFF]),
              "USDJPY": svr.sf("USDJPY", -4.95, "pct", [svr.TARIFF]),
              "IG_OAS": svr.sf("IG_OAS", 4.0, "bp", [svr.DEEPSEEK]),
              "HY_OAS": svr.sf("HY_OAS", 30.0, "bp", [svr.NOTE])}
    chunks = [svr.chunk(d) for d in (svr.TARIFF, svr.DEEPSEEK, svr.NOTE)]
    diag = SynthesisDiagnostics()
    check_episode_mix(fields, chunks, diag=diag)
    assert set(fields) == {"UST_10Y", "USDJPY", "IG_OAS", "HY_OAS"}
    for n in ("UST_10Y", "USDJPY", "IG_OAS"):
        assert fields[n].validation == [svr.CODE], n
    assert fields["HY_OAS"].validation == []
    assert svr.warns(diag) == [{"code": svr.CODE, "episodes": {
        "market_episode_tariff": ["USDJPY", "UST_10Y"],
        "market_episode_deepseek": ["IG_OAS"]}}]
    assert svr.CODE not in HARD_VALIDATION_CODES


@pytest.mark.criterion("C2")
def test_single_episode_or_uncited_is_inert_but_mixed_is_not(svr):
    # oracle: the Definitions by hand. One episode (plus prose) changes nothing; a cited
    # chunk missing from the retrieved list counts for nothing; an existing code is not
    # duplicated. The last block mixes two episodes, so a do-nothing check fails.
    chunks = [svr.chunk(d) for d in (svr.TARIFF, svr.DEEPSEEK, svr.NOTE)]
    one = {"UST_10Y": svr.sf("UST_10Y", -20.0, "bp", [svr.TARIFF]),
           "HY_OAS": svr.sf("HY_OAS", 30.0, "bp", [svr.NOTE])}
    diag = SynthesisDiagnostics()
    check_episode_mix(one, chunks, diag=diag)
    assert [f.validation for f in one.values()] == [[], []] and svr.warns(diag) == []

    not_retrieved = {"UST_10Y": svr.sf("UST_10Y", -20.0, "bp", [svr.TARIFF]),
                     "IG_OAS": svr.sf("IG_OAS", 4.0, "bp", [svr.DEEPSEEK])}
    check_episode_mix(not_retrieved, [svr.chunk(svr.TARIFF)])
    assert [f.validation for f in not_retrieved.values()] == [[], []]

    mixed = {"UST_10Y": svr.sf("UST_10Y", -20.0, "bp", [svr.TARIFF],
                               validation=[svr.CODE]),
             "IG_OAS": svr.sf("IG_OAS", 4.0, "bp", [svr.DEEPSEEK])}
    check_episode_mix(mixed, chunks)
    assert mixed["UST_10Y"].validation == [svr.CODE]
    assert mixed["IG_OAS"].validation == [svr.CODE]


@pytest.mark.criterion("C2")
@settings(max_examples=60, deadline=None)
@given(assign=st.dictionaries(st.sampled_from(["VIX", "UST_2Y", "UST_10Y", "IG_OAS",
                                               "USDTWD"]),
                              st.sampled_from(["market_episode_tariff",
                                               "market_episode_deepseek", "credit_note"]),
                              min_size=1))
def test_property_flags_iff_two_episodes(svr, assign):
    # oracle: the Definitions computed in the test. A control pair (HY_OAS on tariff,
    # USDJPY on DeepSeek) is always present, so every case mixes two episodes and a
    # do-nothing check fails.
    a = dict(assign, HY_OAS="market_episode_tariff", USDJPY="market_episode_deepseek")
    fields = {n: svr.sf(n, 1.0, "bp", [d]) for n, d in a.items()}
    chunks = [svr.chunk(d) for d in (svr.TARIFF, svr.DEEPSEEK, svr.NOTE)]
    diag = SynthesisDiagnostics()
    check_episode_mix(fields, chunks, diag=diag)
    episodes: dict[str, list[str]] = {}
    for n, d in a.items():
        if d.startswith("market_episode"):
            episodes.setdefault(d, []).append(n)
    for n, d in a.items():
        assert fields[n].validation == ([svr.CODE] if d.startswith("market_episode")
                                        else []), n
    assert svr.warns(diag) == [{"code": svr.CODE,
                                "episodes": {k: sorted(v) for k, v in episodes.items()}}]
