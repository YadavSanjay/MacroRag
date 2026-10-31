"""C3: the two Fed endpoints carry inflation, growth and labor, plus their old tags."""

from __future__ import annotations

import pytest
import yaml

# oracle: spec C3 — the existing tags (configs/ingest/tier1_fed.yaml at the time of the
# spec) plus inflation, growth, labor.
OLD = {"press_monetary": ["discount_rate", "term_premium", "rates_vol"],
       "speeches": ["discount_rate", "rates_vol", "forward_guidance"]}
ADDED = {"inflation", "growth", "labor"}


def _endpoints(rt) -> dict:
    data = yaml.safe_load((rt.REPO_ROOT / "configs" / "ingest" / "tier1_fed.yaml")
                          .read_text(encoding="utf-8")) or {}
    return {ep.get("id"): ep for ep in data.get("endpoints") or [] if isinstance(ep, dict)}


@pytest.mark.criterion("C3")
def test_fed_endpoints_carry_the_macro_tags(rt):
    # oracle: spec C3 — each endpoint's factor_tags is a superset of its old tags plus
    # inflation, growth and labor.
    eps = _endpoints(rt)
    for eid, old in OLD.items():
        tags = set(eps[eid].get("factor_tags") or [])
        assert ADDED <= tags, (eid, sorted(ADDED - tags))
        assert set(old) <= tags, (eid, sorted(set(old) - tags))


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
def test_tags_are_vocabulary_and_typed_config_agrees(rt):
    # oracle: configs/ingest/vocabulary.yaml's factor_tags list. Relation: the typed
    # config loader yields the same tag sets as the raw YAML for both endpoints, and
    # both include the added tags.
    from ingest.common.config import load_tier_config
    vocab = set((yaml.safe_load((rt.REPO_ROOT / "configs" / "ingest" / "vocabulary.yaml")
                                .read_text(encoding="utf-8")) or {}).get("factor_tags") or [])
    raw = _endpoints(rt)
    typed = {ep.id: ep for ep in load_tier_config("tier1_fed").endpoints}
    for eid in OLD:
        tags = set(raw[eid].get("factor_tags") or [])
        assert tags <= vocab, (eid, sorted(tags - vocab))
        assert set(typed[eid].factor_tags) == tags
        assert ADDED <= set(typed[eid].factor_tags)
