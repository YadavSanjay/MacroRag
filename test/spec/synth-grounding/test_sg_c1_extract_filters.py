"""C1: vocabulary cues match only as whole words (optional plural `s`)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from retriever.analysis import extract_filters

_LOWER = "abcdefghijklmnopqrstuvwxyz"


@pytest.mark.criterion("C1")
def test_spec_examples_exact_tag_sets():
    # oracle: the spec's three example strings with `\b` applied by hand:
    #  "PRC maritime/air blockade around the Taiwan Strait disrupts supply chains"
    #    - "ai" occurs only inside m-ai-time, ai-r, Str-ai-t, ch-ai-ns: each has a
    #      letter on at least one side, so no word boundary -> no `ai` entity tag;
    #    - "taiwan" is a whole word -> factor china_taiwan_tail.
    #  "AI capex and hyperscaler guidance" -> whole words AI, hyperscaler.
    #  "export bans on chips" -> "chip" + plural "s" then a boundary -> semiconductors.
    blockade = extract_filters(
        "PRC maritime/air blockade around the Taiwan Strait disrupts supply chains")
    assert "ai" not in blockade.entity_tags
    assert "china_taiwan_tail" in blockade.factor_tags

    capex = extract_filters("AI capex and hyperscaler guidance")
    assert set(capex.entity_tags) == {"ai", "hyperscaler"}

    chips = extract_filters("export bans on chips")
    assert set(chips.entity_tags) == {"semiconductors"}


@pytest.mark.criterion("C1")
def test_cue_inside_longer_word_yields_no_tag_point_cases():
    # oracle: `\b` by hand. "air" (ai+r), "chains" (ch+ai+ns), "Strait", "detail",
    # "chipset" (chip+set: no boundary after "chip", and "s" is followed by "e"),
    # "chinaware" (china+ware), "quantumly" contain cues only inside longer words.
    # "AIs" is the cue plus one plural "s" then a boundary -> ai.
    for text in ["air", "chains", "Strait", "detail", "chipset", "chinaware",
                 "quantumly"]:
        assert extract_filters(text).entity_tags == [], text
    assert set(extract_filters("AIs").entity_tags) == {"ai"}


@pytest.mark.criterion("C1")
@given(prefix=st.text(alphabet=_LOWER, min_size=1, max_size=6),
       suffix=st.text(alphabet=_LOWER, max_size=6))
def test_ai_embedded_in_a_word_never_tags(prefix, suffix):
    # oracle: the word is `prefix + "ai" + suffix` with a non-empty letter prefix,
    # so "ai" never starts at a word boundary -> no `ai` tag (C1 rule). No entity
    # cue other than "ai" itself contains "ai", so no cue can equal the whole word.
    word = f"{prefix}ai{suffix}"
    assert "ai" not in extract_filters(word).entity_tags


# Entity cues that do not themselves contain another cue as a whole word
# ("artificial intelligence" is excluded: it maps to `ai`, which the test
# asserts is absent). cue -> the tag the spec's rule gives it.
_ENTITY_CUES = {
    "hyperscaler": "hyperscaler", "data center": "data_center",
    "semiconductor": "semiconductors", "chip": "semiconductors",
    "china": "china", "quantum": "quantum",
}


@pytest.mark.criterion("C1")
@given(cue=st.sampled_from(sorted(_ENTITY_CUES)),
       upper=st.lists(st.booleans(), min_size=16, max_size=16),
       plural=st.booleans(),
       prefix=st.text(alphabet=_LOWER, min_size=1, max_size=5),
       suffix=st.text(alphabet=_LOWER, max_size=5),
       lead=st.sampled_from(["", "the ", "(", "on "]),
       trail=st.sampled_from(["", " risk", ".", ")", "/x"]))
def test_whole_word_cue_tags_and_embedded_ai_does_not(cue, upper, plural, prefix,
                                                      suffix, lead, trail):
    # oracle: C1 rule by hand. The cue (any letter case, optionally + one "s") is
    # delimited by non-word characters, so it starts and ends at a boundary -> its
    # tag. The second word `prefix+"ai"+suffix` holds "ai" only inside a longer
    # word -> no `ai` tag. The fillers ("the", "risk", "x", ...) carry no cue.
    cased = "".join(ch.upper() if upper[i % len(upper)] else ch
                    for i, ch in enumerate(cue))
    text = f"{lead}{cased}{'s' if plural else ''}{trail} {prefix}ai{suffix}"
    tags = set(extract_filters(text).entity_tags)
    assert tags == {_ENTITY_CUES[cue]}
