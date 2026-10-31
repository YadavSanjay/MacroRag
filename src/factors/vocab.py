"""Controlled-vocabulary helpers for factor discovery.

Factor discovery is constrained to MacroRAG's existing controlled ``factor_tags``
/ ``entity_tags`` registry (configs/vocabulary.yaml) so its output composes
directly with ``/query`` filters — a discovered ``factor_tag`` can be fed straight
back as a filter. We do NOT invent a parallel taxonomy here.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _vocab() -> tuple[frozenset[str], frozenset[str], dict[str, str]]:
    from ingest.common.config import load_vocabulary
    v = load_vocabulary()
    return (frozenset(v["factor_tags"]), frozenset(v["entity_tags"]),
            dict(v["aliases"]))


def valid_factor_tags() -> frozenset[str]:
    return _vocab()[0]


def valid_entity_tags() -> frozenset[str]:
    return _vocab()[1]


def canonicalize(tag: str) -> str:
    """Lowercase + apply the alias map (lenient; unknown tags pass through)."""
    aliases = _vocab()[2]
    low = str(tag).lower()
    return aliases.get(tag, aliases.get(low, low))


def factor_reference() -> str:
    """The controlled factor-tag list for the discovery prompt (so the LLM picks
    from the canonical vocabulary rather than free-text labels)."""
    return ", ".join(sorted(valid_factor_tags()))
