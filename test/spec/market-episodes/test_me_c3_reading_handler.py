"""C3: read_fred_rows reads the stored FRED documents; the handler emits cards offline."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ingest.usstats.episodes import read_fred_rows
from utils.objectstore import InMemoryObjectStore


def _key(r: dict) -> tuple[str, str]:
    return (r["series_code"], r["date"])


@pytest.mark.criterion("C3")
def test_read_fred_rows_prefix_suffix_and_later_retrieval_wins(me):
    # oracle: mapped by hand. Read: every *.json under tier3/fred_daily/.
    # DGS2 09-15 is in two docs: retrieval 2026-10-14 (1.80, key ..._2008.json)
    # beats 2026-01-05 (1.99, key ..._2008_old.json, sorted after it).
    # VIXCLS 09-15 is in two docs: 2026-02-01 (31.50, key ..._v2.json, sorted after)
    # beats 2025-12-01 (30.00). Decoys never read: tier3/bea/ (other source),
    # tier3/fred_daily_archive/ (sibling prefix, not under tier3/fred_daily/),
    # tier3/fred_daily/notes.txt (not .json) -- each would add a 9.99 row.
    r = me.row
    s = InMemoryObjectStore()
    s.put_text("tier3/fred_daily/fred_daily_dgs2_2008.json", me.fred_doc(
        "fred_daily_dgs2_2008", "2026-10-14",
        [r("DGS2", "2008-09-12", 2.21, "percent"), r("DGS2", "2008-09-15", 1.80, "percent")]))
    s.put_text("tier3/fred_daily/fred_daily_dgs2_2008_old.json", me.fred_doc(
        "fred_daily_dgs2_2008", "2026-01-05",
        [r("DGS2", "2008-09-15", 1.99, "percent"), r("DGS2", "2008-09-16", 1.85, "percent")]))
    s.put_text("tier3/fred_daily/fred_daily_vixcls_2008.json", me.fred_doc(
        "fred_daily_vixcls_2008", "2025-12-01",
        [r("VIXCLS", "2008-09-12", 25.0, "index_points"),
         r("VIXCLS", "2008-09-15", 30.0, "index_points")]))
    s.put_text("tier3/fred_daily/fred_daily_vixcls_2008_v2.json", me.fred_doc(
        "fred_daily_vixcls_2008", "2026-02-01",
        [r("VIXCLS", "2008-09-15", 31.5, "index_points")]))
    s.put_text("tier3/bea/nipa.json", me.fred_doc(
        "nipa", "2026-10-14", [r("DGS2", "2008-09-17", 9.99, "percent")]))
    s.put_text("tier3/fred_daily_archive/x.json", me.fred_doc(
        "x", "2026-10-14", [r("DGS2", "2008-09-18", 9.99, "percent")]))
    s.put_text("tier3/fred_daily/notes.txt", me.fred_doc(
        "notes", "2026-10-14", [r("DGS2", "2008-09-19", 9.99, "percent")]))

    got = read_fred_rows(s)
    assert isinstance(got, list)
    assert sorted(got, key=_key) == [
        r("DGS2", "2008-09-12", 2.21, "percent"),
        r("DGS2", "2008-09-15", 1.80, "percent"),
        r("DGS2", "2008-09-16", 1.85, "percent"),
        r("VIXCLS", "2008-09-12", 25.0, "index_points"),
        r("VIXCLS", "2008-09-15", 31.5, "index_points"),
    ]


_SERIES = ["AAA", "BBB"]
_DATES = ["2020-01-02", "2020-01-03", "2020-01-06"]


@pytest.mark.criterion("C3")
@given(data=st.data())
def test_property_union_with_latest_retrieval_winning(me, data):
    # oracle: closed form. Up to five documents, each with a distinct retrieval date
    # (in an order unrelated to key order) and a subset of (series, date) cells whose
    # value encodes (document, cell). Expected: one row per cell present in any
    # document, taken from the document with the latest retrieval date.
    n = data.draw(st.integers(min_value=1, max_value=5))
    days = data.draw(st.permutations(list(range(1, n + 1))))
    cells = [(s, d) for s in _SERIES for d in _DATES]
    store = InMemoryObjectStore()
    best: dict[tuple[str, str], tuple[int, dict]] = {}
    for i in range(n):
        mine = data.draw(st.lists(st.sampled_from(cells), unique=True, max_size=6))
        retrieval = date(2026, 1, days[i]).isoformat()
        rows = [me.row(s, d, float(100 * i + cells.index((s, d))), "percent")
                for s, d in mine]
        store.put_text(f"tier3/fred_daily/doc_{i}.json",
                       me.fred_doc(f"doc_{i}", retrieval, rows))
        for rw in rows:
            if _key(rw) not in best or best[_key(rw)][0] < days[i]:
                best[_key(rw)] = (days[i], rw)
    expected = [best[c][1] for c in sorted(best)]
    assert sorted(read_fred_rows(store), key=_key) == expected


def _lehman_files(me) -> dict[str, str]:
    files = {}
    for series, rows in me.LEHMAN_ROWS.items():
        files[f"tier3/fred_daily/fred_daily_{series.lower()}_2008.json"] = me.fred_doc(
            f"fred_daily_{series.lower()}_2008", "2026-10-13", rows)
    # An older retrieval of VIXCLS (sorted after the newer key) with a 09-17 value
    # that would change the 5-day peak: the 2026-10-13 retrieval must win.
    files["tier3/fred_daily/fred_daily_vixcls_2008_stale.json"] = me.fred_doc(
        "fred_daily_vixcls_2008", "2026-01-05",
        [me.row("VIXCLS", "2008-09-17", 50.0, "index_points")])
    # A decoy outside tier3/fred_daily/ that would change UST_2Y if read.
    files["tier3/bea/decoy.json"] = me.fred_doc(
        "decoy", "2026-10-14", [me.row("DGS2", "2008-09-19", 9.0, "percent")])
    return files


@pytest.mark.criterion("C3")
def test_handler_reads_local_store_and_returns_the_card(me, tmp_path):
    # oracle: the hand-worked Lehman card (conftest) — the handler reads the rows
    # from <root>/tier3/fred_daily/*.json through the local object store and returns
    # exactly [that card] for lehman, and [] for china_deval (no rows near its
    # anchor). discover yields one item per configured episode. Every HTTP call
    # raises, so passing proves no request.
    me.write_files(tmp_path, _lehman_files(me))
    quiet = {"id": "china_deval", "name": "China yuan devaluation",
             "anchor": date(2015, 8, 10), "factor_tags": ["fx"], "entity_tags": ["china"]}
    cfg = me.endpoint([dict(me.LEHMAN), quiet])
    items, out = me.run_handler(cfg, tmp_path)
    assert len(items) == 2
    assert sorted(len(docs) for docs in out) == [0, 1]
    (card,) = [d for docs in out for d in docs]
    me.assert_lehman_card(card)


@pytest.mark.criterion("C3")
@given(n=st.integers(min_value=0, max_value=4))
def test_property_one_item_per_episode_and_no_rows_no_card(me, n):
    # oracle: spec C3 — discover returns one item per configured episode; with an
    # empty store every build returns None, so every fetch returns []. No HTTP.
    eps = [{"id": f"ep{i}", "name": f"Episode {i}", "anchor": date(2020, 1, 2 + i),
            "factor_tags": [], "entity_tags": []} for i in range(n)]
    with tempfile.TemporaryDirectory() as root:
        items, out = me.run_handler(me.endpoint(eps), Path(root))
    assert len(items) == n
    assert out == [[] for _ in range(n)]
