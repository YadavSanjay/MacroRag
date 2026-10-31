"""C4: REST — QueryRequest.no_cache on POST /query (both modes) and POST /query/stream."""

from __future__ import annotations

import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# pydantic's lax bool parsing accepts these strings (case-insensitive); anything else 422s.
_PYDANTIC_BOOL_STRINGS = {"0", "off", "f", "false", "n", "no", "1", "on", "t", "true", "y", "yes"}


def _client(acb, root):
    acb.assert_request_field()
    return acb.rest_client(acb.build(root, l1=acb.mem_l1()))


def _post(client, mode, **extra):
    r = client.post("/query", json={"question": "how did treasury yields behave",
                                    "mode": mode, **extra})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("mode", ["answer", "synthesize"])
def test_query_no_cache_neither_reads_nor_writes(acb, tmp_path, mode):
    # oracle: spec C4 — {"question": q} then {..., "no_cache": true}: the second response
    # has cached false; {..., "no_cache": true} then {"question": q}: the second has
    # cached false; in synthesize mode the key is the embedded query result's.
    client = _client(acb, tmp_path / "read")
    first = _post(client, mode)
    assert first["refused"] is False
    assert _post(client, mode, no_cache=True)["cached"] is False

    client = _client(acb, tmp_path / "write")
    _post(client, mode, no_cache=True)
    assert _post(client, mode)["cached"] is False


@pytest.mark.criterion("C4")
def test_stream_no_cache_after_identical_query(acb, tmp_path):
    # oracle: spec C4 — POST /query/stream with no_cache true after an identical
    # POST /query: its retrieved and answer events have cached false.
    client = _client(acb, tmp_path)
    _post(client, "answer")
    r = client.post("/query/stream", json={"question": "how did treasury yields behave",
                                           "no_cache": True})
    assert r.status_code == 200, r.text
    events = acb.sse_events(r.text)
    assert events["retrieved"]["cached"] is False
    assert events["answer"]["cached"] is False
    assert events["answer"]["refused"] is False


@pytest.mark.criterion("C4")
def test_no_cache_must_be_a_bool(acb, tmp_path):
    # oracle: spec C4 + FastAPI/pydantic's documented 422 on a field that fails
    # validation: "maybe" is not a bool.
    client = acb.rest_client(acb.build(tmp_path, l1=acb.mem_l1()))
    r = client.post("/query", json={"question": "how did treasury yields behave",
                                    "no_cache": "maybe"})
    assert r.status_code == 422, r.text
    assert "no_cache" in r.text


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_stream_without_flag_is_cached_but_with_flag_is_not(acb, tmp_path):
    # oracle: spec C4. Relation: after an identical POST /query, the stream without
    # no_cache is served from the cache (cached true, as today) while the stream with
    # no_cache true is not (cached false); both answer the same question unrefused.
    client = _client(acb, tmp_path)
    _post(client, "answer")
    plain = acb.sse_events(client.post(
        "/query/stream", json={"question": "how did treasury yields behave"}).text)
    bypass = acb.sse_events(client.post(
        "/query/stream", json={"question": "how did treasury yields behave",
                               "no_cache": True}).text)
    assert (plain["answer"]["cached"], bypass["answer"]["cached"]) == (True, False)
    assert plain["answer"]["refused"] is bypass["answer"]["refused"] is False


@pytest.mark.criterion("C4")
@settings(max_examples=20, deadline=None)
@given(mode=st.sampled_from(["answer", "synthesize"]),
       flags=st.lists(st.booleans(), min_size=2, max_size=4))
def test_property_rest_cached_follows_the_bypass_model(acb, mode, flags):
    # oracle: spec C4 with the C2 model computed in the test (conftest expected_cached):
    # each response's cached is true iff no_cache is false and an earlier no_cache-false
    # request of the same mode stored it.
    with tempfile.TemporaryDirectory() as root:
        client = _client(acb, root)
        got = [_post(client, mode, no_cache=f)["cached"] for f in flags]
    assert got == acb.expected_cached(flags)


@pytest.mark.criterion("C4")
@settings(max_examples=10, deadline=None)
@given(value=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=2, max_size=8)
       .filter(lambda s: s not in _PYDANTIC_BOOL_STRINGS))
def test_property_non_bool_strings_are_422(acb, value):
    # oracle: FastAPI/pydantic's documented 422 on a field that fails validation; a
    # lowercase word outside pydantic's accepted bool strings is not a bool.
    with tempfile.TemporaryDirectory() as root:
        client = acb.rest_client(acb.build(root, l1=acb.mem_l1()))
        r = client.post("/query", json={"question": "how did treasury yields behave",
                                        "no_cache": value})
    assert r.status_code == 422, r.text
