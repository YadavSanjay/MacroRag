"""C5: tier3_usstats.yaml configures market_episodes; the registry resolves `episodes`."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from ingest.usstats.handlers import HANDLERS
from ingest.usstats.handlers.episodes import MarketEpisodeHandler

# oracle: the spec's C5 table, copied verbatim.
EXPECTED = {
    "lehman": ("Lehman Brothers bankruptcy", date(2008, 9, 12)),
    "us_downgrade": ("US sovereign downgrade", date(2011, 8, 5)),
    "china_deval": ("China yuan devaluation", date(2015, 8, 10)),
    "volmageddon": ("Volmageddon short-vol unwind", date(2018, 2, 1)),
    "covid": ("COVID-19 market crash", date(2020, 2, 21)),
    "svb": ("Silicon Valley Bank failure", date(2023, 3, 8)),
    "yen_carry": ("Yen carry-trade unwind", date(2024, 7, 31)),
    "deepseek": ("DeepSeek AI selloff", date(2025, 1, 24)),
    "tariff": ("Liberation Day tariff shock", date(2025, 4, 2)),
}


def _endpoints(me) -> list[dict]:
    path = me.REPO_ROOT / "configs" / "ingest" / "tier3_usstats.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [ep for ep in data.get("endpoints") or [] if isinstance(ep, dict)]


def _episodes(me) -> list[dict]:
    eps = [ep for ep in _endpoints(me) if ep.get("id") == "market_episodes"]
    assert len(eps) == 1, "tier3_usstats.yaml has no (single) market_episodes endpoint"
    return list((eps[0].get("content") or {}).get("episodes") or [])


def _as_date(x) -> date | None:
    """An anchor as YAML gives it: a date (unquoted) or an ISO string (quoted)."""
    if isinstance(x, date):
        return x
    return date.fromisoformat(x) if isinstance(x, str) else None


def _vocab(me) -> dict:
    path = me.REPO_ROOT / "configs" / "ingest" / "vocabulary.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@pytest.mark.criterion("C5")
def test_market_episodes_endpoint_and_triples(me):
    # oracle: spec C5 — handler `episodes`, listed after fred_daily, and the
    # nine (id, name, anchor) triples of the table above, which the list must include.
    # Relaxed from "exactly" when spec episode-russia-ukraine added a tenth episode;
    # that spec pins the tenth and the order of the list.
    ids = [ep.get("id") for ep in _endpoints(me)]
    assert "market_episodes" in ids, "no market_episodes endpoint"
    assert "fred_daily" in ids
    assert ids.index("market_episodes") > ids.index("fred_daily")
    ep = _endpoints(me)[ids.index("market_episodes")]
    assert ep.get("handler") == "episodes"
    triples = [(e.get("id"), e.get("name"), _as_date(e.get("anchor")))
               for e in _episodes(me)]
    assert {(i, n, a) for i, (n, a) in EXPECTED.items()} <= set(triples)


@pytest.mark.criterion("C5")
def test_episode_tags_come_from_the_vocabulary(me):
    # oracle: configs/ingest/vocabulary.yaml's factor_tags / entity_tags lists, and
    # spec C5's deepseek rule (entity tags include ai and semiconductors).
    vocab = _vocab(me)
    factors = {t for t in vocab.get("factor_tags") or [] if isinstance(t, str)}
    entities = {t for t in vocab.get("entity_tags") or [] if isinstance(t, str)}
    eps = _episodes(me)
    assert {e.get("id") for e in eps} >= set(EXPECTED)
    for e in eps:
        ft, et = e.get("factor_tags"), e.get("entity_tags")
        assert isinstance(ft, list) and isinstance(et, list), e
        assert set(ft) <= factors, (e.get("id"), set(ft) - factors)
        assert set(et) <= entities, (e.get("id"), set(et) - entities)
    deepseek = next(e for e in eps if e.get("id") == "deepseek")
    assert {"ai", "semiconductors"} <= set(deepseek["entity_tags"])


@pytest.mark.criterion("C5")
def test_registry_resolves_episodes_to_handler():
    # oracle: spec C5 — "The tier's registry resolves episodes to MarketEpisodeHandler".
    from ingest.usstats.ingestor import UsStatsIngestor
    assert HANDLERS.get("episodes") is MarketEpisodeHandler
    assert UsStatsIngestor.handler_registry.get("episodes") is MarketEpisodeHandler


@pytest.mark.criterion("C5")
@given(eid=st.sampled_from(sorted(set(EXPECTED) - {"deepseek"})))
def test_property_committed_config_drives_the_handler(me, eid):
    # oracle: the C5 table (name, anchor) + the Definitions by hand. The committed
    # endpoint, loaded through the typed config and run by the handler the registry
    # resolves, builds the card of every listed episode: one VIXCLS series with
    # v0 = 20.00 on the anchor and 20 + j on anchor + j days (j = 1..10) gives
    # +5.00 / +5.00 at 5 days (last anchor+5) and +10.00 / +10.00 at 10 days.
    # Relaxed by spec episode-card-horizons (an episode may configure its own horizons,
    # and deepseek now sets [1, 5, 10]): samples only the listed episodes that configure
    # no horizons, so excludes deepseek; that spec pins deepseek's card.
    from ingest.common.config import load_tier_config
    from ingest.usstats.ingestor import UsStatsIngestor

    tier = load_tier_config("tier3_usstats")
    eps = [ep for ep in tier.endpoints if ep.id == "market_episodes"]
    assert len(eps) == 1, "tier3_usstats.yaml has no market_episodes endpoint"
    cfg = eps[0]
    assert UsStatsIngestor.handler_registry.get(cfg.handler) is MarketEpisodeHandler
    conf = next(e for e in cfg.content["episodes"] if e.get("id") == eid)

    name, a = EXPECTED[eid]
    rows = [me.row("VIXCLS", (a + timedelta(days=j)).isoformat(), 20.0 + j, "index_points")
            for j in range(11)]
    with tempfile.TemporaryDirectory() as root:
        me.write_files(Path(root), {"tier3/fred_daily/fred_daily_vixcls.json":
                                    me.fred_doc("fred_daily_vixcls", "2026-10-14", rows)})
        items, out = me.run_handler(cfg, Path(root))
    assert len(items) == len(cfg.content["episodes"])
    cards = [d for docs in out for d in docs]
    assert len(cards) == 1, [d.doc_id for d in cards]
    d5, d10 = (a + timedelta(days=5)).isoformat(), (a + timedelta(days=10)).isoformat()
    tags = list(conf["factor_tags"])
    if "cross_asset_scenario" not in tags:
        tags.append("cross_asset_scenario")
    ep = {"id": eid, "name": name, "anchor": a, "entity_tags": list(conf["entity_tags"])}
    me.assert_card(
        cards[0], ep=ep, today=me.TODAY, release=a + timedelta(days=10),
        moves5={"VIXCLS": me.move("VIX", "pts", 5.0, 5.0, d5)},
        moves10={"VIXCLS": me.move("VIX", "pts", 10.0, 10.0, d10)},
        chunks=[(k, "\n".join([
            f"Market episode: {name}. Realized moves over the {k} trading days after "
            f"the {a.isoformat()} close, from FRED daily data.",
            f"- VIX (VIXCLS): +{k}.00 pts after {k} trading days ({dk}); "
            f"largest move within the window +{k}.00 pts"]))
            for k, dk in ((5, d5), (10, d10))],
        factor_tags=tags)
