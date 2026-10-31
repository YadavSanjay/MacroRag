"""C1: parse_fred_observations flattens a FRED observations body into sorted rows."""

from __future__ import annotations

import json
import math
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ingest.usstats.parsers import parse_fred_observations


def _body(observations) -> bytes:
    return json.dumps({"units": "lin", "observations": observations}).encode()


def _obs(d: str, v: str) -> dict:
    return {"realtime_start": "2026-10-14", "realtime_end": "2026-10-14",
            "date": d, "value": v}


@pytest.mark.criterion("C1")
def test_rows_sorted_and_unusable_observations_skipped(fred):
    # oracle: hand-written body below, mapped by hand to the spec's Row shape:
    # "." / "" / non-numeric / nan / inf values and invalid ISO dates are skipped,
    # the rest sorted by date ascending with the value parsed as float.
    body = _body([
        _obs("2025-03-04", "17.25"),
        _obs("2025-03-03", "."),            # FRED missing marker -> skipped
        _obs("2025-01-02", "16.5"),
        _obs("2025-01-03", ""),             # empty -> skipped
        _obs("2025-02-14", "n/a"),          # not a number -> skipped
        _obs("2025-02-17", "NaN"),          # not finite -> skipped
        _obs("2025-02-18", "inf"),          # not finite -> skipped
        _obs("2025-02-30", "15.0"),         # not a valid date -> skipped
        _obs("2025/02/10", "15.5"),         # not ISO -> skipped
        _obs("2024-12-31", "-0.25"),
        _obs("2025-02-03", "1e1"),
    ])
    rows = parse_fred_observations("VIXCLS", "index_points", body)
    fred.assert_rows(rows, [
        fred.row("VIXCLS", "2024-12-31", -0.25, "index_points"),
        fred.row("VIXCLS", "2025-01-02", 16.5, "index_points"),
        fred.row("VIXCLS", "2025-02-03", 10.0, "index_points"),
        fred.row("VIXCLS", "2025-03-04", 17.25, "index_points"),
    ])


@pytest.mark.criterion("C1")
def test_series_and_units_are_stamped_verbatim(fred):
    # oracle: the spec's Row definition — source "FRED", series_code and units are
    # the arguments passed in; one observation maps to one row.
    body = _body([_obs("2023-10-16", "1.23")])
    rows = parse_fred_observations("BAMLC0A0CM", "percent", body)
    fred.assert_rows(rows, [fred.row("BAMLC0A0CM", "2023-10-16", 1.23, "percent")])


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("body", [
    b"<html>Service unavailable</html>",                 # not JSON
    b"",                                                 # not JSON
    json.dumps({"error_code": 400, "error_message": "Bad Request."}).encode(),
    json.dumps({"observations": {"date": "2025-01-02", "value": "1"}}).encode(),
    json.dumps({"observations": None}).encode(),
    json.dumps([{"date": "2025-01-02", "value": "1"}]).encode(),
    b"null",
])
def test_malformed_body_raises_value_error(body):
    # oracle: spec C1 — a body that isn't JSON, or has no `observations` list,
    # raises ValueError.
    with pytest.raises(ValueError):
        parse_fred_observations("DGS10", "percent", body)


@pytest.mark.criterion("C1")
def test_empty_observations_list_gives_no_rows():
    # oracle: spec C1 — rows are one per (usable) observation; an empty list is a
    # valid `observations` list, so no rows and no error.
    # red-exempt: empty the correct answer for an empty observations list is []
    rows = parse_fred_observations("SOFR", "percent", _body([]))
    assert rows == []
    assert isinstance(rows, list)


_DATES = st.dates(min_value=date(1990, 1, 1), max_value=date(2026, 12, 31))
_VALUES = st.floats(allow_nan=False, allow_infinity=False, width=64)
_JUNK = st.sampled_from([".", "", "NaN", "-inf", "Infinity", "abc", "1.2.3"])


@pytest.mark.criterion("C1")
@given(
    good=st.dictionaries(_DATES, _VALUES, min_size=1, max_size=25),
    junk=st.dictionaries(_DATES, _JUNK, max_size=10),
    order=st.randoms(use_true_random=False),
)
def test_property_rows_are_the_usable_observations_in_date_order(fred, good, junk, order):
    # oracle: closed form from the spec — the expected rows are exactly the finite
    # observations, sorted by date; junk values are dropped wherever they sit and
    # the input order does not matter.
    junk = {d: v for d, v in junk.items() if d not in good}
    obs = ([_obs(d.isoformat(), repr(v)) for d, v in good.items()]
           + [_obs(d.isoformat(), v) for d, v in junk.items()])
    order.shuffle(obs)
    rows = parse_fred_observations("DGS2", "percent", _body(obs))
    expected = [fred.row("DGS2", d.isoformat(), v, "percent")
                for d, v in sorted(good.items())]
    fred.assert_rows(rows, expected)
    assert all(math.isfinite(r["value"]) for r in rows)
