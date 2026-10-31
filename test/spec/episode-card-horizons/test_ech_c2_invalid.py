"""C2: build_episode_document refuses an episode whose horizons are not valid."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ingest.usstats.episodes import build_episode_document

# oracle: spec C2's examples, verbatim, plus two mixed lists that break one rule each
# (a non-positive element; a float among ints).
_INVALID = [[], [0], [-5], [5, 5], [5.0], [True], "5", None, [5, 0], [1, 5, 2.5]]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("horizons", _INVALID, ids=repr)
def test_spec_examples_raise(ech, horizons):
    # oracle: the Definitions — valid horizons are a non-empty list of distinct ints
    # >= 1, bools excluded. Each example breaks that; the Lehman rows move at 5 and 10,
    # so only the horizons can make the call fail.
    with pytest.raises(ValueError):
        build_episode_document(ech.lehman(horizons), ech.lehman_rows(), today=ech.TODAY)


_ok = st.lists(st.integers(min_value=1, max_value=30), min_size=1, max_size=4, unique=True)


def _with(bad):
    """A valid list with one bad element inserted somewhere."""
    return st.tuples(_ok, bad, st.integers(min_value=0, max_value=4)).map(
        lambda t: t[0][:t[2]] + [t[1]] + t[0][t[2]:])


_invalid = st.one_of(
    st.just([]),
    _with(st.integers(max_value=0)),                         # an element < 1
    _with(st.floats(allow_nan=False)),                       # a float, even 5.0
    _with(st.booleans()),                                    # a bool is not an int here
    _with(st.text(max_size=3)),                              # a non-number
    _ok.map(lambda hs: hs + [hs[0]]),                        # a duplicate
    st.none(),                                               # not a list
    st.integers(min_value=1, max_value=30),
    st.text(max_size=3),
    st.dictionaries(st.text(max_size=2), st.integers(min_value=1, max_value=30),
                    max_size=2),
)


@pytest.mark.criterion("C2")
@given(horizons=_invalid)
def test_property_any_invalid_horizons_raise(ech, horizons):
    # oracle: the Definitions. Every generated value breaks at least one rule (empty, an
    # element < 1, a float or bool or str element, a duplicate, or not a list at all),
    # so the call must raise ValueError on rows that would otherwise build a card.
    with pytest.raises(ValueError):
        build_episode_document(ech.lehman(horizons), ech.lehman_rows(), today=ech.TODAY)
