"""C3: run_incremental consults should_cancel before loading the embedder or writing; a raising
callback propagates and leaves the store and manifest untouched; a non-raising one changes
nothing about a dry run's stats."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


class SpecStopError(Exception):
    """The test's own cancellation signal."""


class Canceller:
    """should_cancel stand-in: counts calls and raises SpecStopError on call ``raise_on`` (1-based;
    0 = never raises)."""

    def __init__(self, raise_on: int = 1):
        self.raise_on = raise_on
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        if self.raise_on and self.calls >= self.raise_on:
            raise SpecStopError(f"cancelled at call {self.calls}")


def _run_incremental():
    from pipelines.index_pipeline import run_incremental
    return run_incremental


def _seed(aii, root, sizes: list[int], indexed_docs: int):
    """Corpus of ``sizes``; the first ``indexed_docs`` documents already in the store; a
    pre-existing manifest. Returns settings."""
    s, _ = aii.build_corpus(root, sizes)
    aii.preindex(s, [f"doc{k}" for k in range(indexed_docs)])
    aii.put_manifest(s)
    return s


def _assert_cancel_leaves_state(aii, s, dry_run: bool) -> Canceller:
    before_store = aii.store_state(s)
    before_manifest = aii.manifest_dir_state(s)
    stop = Canceller(raise_on=1)
    with pytest.raises(SpecStopError):
        _run_incremental()(s, dry_run=dry_run, should_cancel=stop)
    aii.release_clients()
    assert stop.calls == 1
    assert aii.store_state(s) == before_store
    assert aii.manifest_dir_state(s) == before_manifest
    assert not s.timeseries_path.exists()
    return stop


@pytest.mark.criterion("C3")
def test_raising_cancel_on_dry_run_propagates_and_writes_nothing(aii, tmp_path):
    # oracle: spec C3 — the exception propagates; store and manifest equal their state before.
    s = _seed(aii, tmp_path, [1, 2], indexed_docs=1)
    assert aii.store_state(s)[0] == 1
    _assert_cancel_leaves_state(aii, s, dry_run=True)


@pytest.mark.criterion("C3")
def test_raising_cancel_on_real_run_stops_before_any_write(aii, tmp_path):
    # oracle: spec C3 / Requirement — called before the embedder is loaded or anything is
    # written, so a real (non-dry) run leaves store, manifest and time-series store untouched.
    # The corpus is fully pre-indexed so today's code (which never calls should_cancel)
    # embeds nothing and fails on the missing exception, not on an embedder call.
    s = _seed(aii, tmp_path, [1, 1], indexed_docs=2)
    assert aii.store_state(s)[0] == 2
    _assert_cancel_leaves_state(aii, s, dry_run=False)


@pytest.mark.criterion("C3")
def test_non_raising_cancel_keeps_dry_run_stats(aii, tmp_path):
    # oracle: spec C3 — same stats with and without f; hand count for corpus [1, 2] with doc0
    # indexed: scanned 3, new 2, unchanged 1, changed 0, indexed 0, deleted 0, stale 0.
    # Requirement: f is consulted (at least once, before the embedder) when given.
    s = _seed(aii, tmp_path, [1, 2], indexed_docs=1)
    run = _run_incremental()
    plain = run(s, dry_run=True)
    aii.release_clients()
    never = Canceller(raise_on=0)
    with_f = run(s, dry_run=True, should_cancel=never)
    aii.release_clients()
    assert with_f == plain
    assert plain == {"scanned": 3, "new": 2, "changed": 0, "unchanged": 1, "indexed": 0,
                     "deleted": 0, "dry_run": True, "stale": 0}
    assert never.calls >= 1


@pytest.mark.criterion("C3")
@settings(max_examples=6)
@given(sizes=st.lists(st.integers(min_value=1, max_value=3), min_size=1, max_size=3),
       data=st.data(), dry_run=st.booleans())
def test_raising_cancel_never_changes_state(aii, sizes, data, dry_run):
    # oracle: spec C3 — for any corpus and store state, a should_cancel raising on its first
    # call propagates and leaves store and manifest as they were. Real runs use a fully
    # pre-indexed corpus (nothing to embed, so no embedder is ever called).
    k = len(sizes) if not dry_run else data.draw(st.integers(0, len(sizes)), label="indexed")
    with aii.temp_root() as root, aii.isolated_env():
        s = _seed(aii, root, sizes, indexed_docs=k)
        _assert_cancel_leaves_state(aii, s, dry_run=dry_run)


@pytest.mark.metamorphic
@pytest.mark.criterion("C3")
@settings(max_examples=6)
@given(sizes=st.lists(st.integers(min_value=1, max_value=3), min_size=1, max_size=3),
       data=st.data())
def test_dry_run_stats_invariant_under_non_raising_cancel(aii, sizes, data):
    # oracle: spec C3 — adding a never-raising should_cancel leaves a dry run's stats equal;
    # scanned/new/unchanged are cross-checked against the corpus the test wrote.
    k = data.draw(st.integers(0, len(sizes)), label="indexed")
    with aii.temp_root() as root, aii.isolated_env():
        s = _seed(aii, root, sizes, indexed_docs=k)
        run = _run_incremental()
        plain = run(s, dry_run=True)
        aii.release_clients()
        never = Canceller(raise_on=0)
        with_f = run(s, dry_run=True, should_cancel=never)
        aii.release_clients()
        assert with_f == plain
        assert plain["scanned"] == sum(sizes)
        assert plain["unchanged"] == sum(sizes[:k])
        assert plain["new"] == sum(sizes[k:])
        assert never.calls >= 1
