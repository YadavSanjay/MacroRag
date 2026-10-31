"""Fixtures for the ingest-fred-daily spec tests.

Fakes sit only at the I/O boundaries a Tier 3 handler touches: the HTTP client
(``ctx.client``), the fetch cache, the raw store and the document store. The fake
client behaves like the FRED ``series/observations`` endpoint: it reads the request
parameters (from the URL query string and/or a ``params=`` mapping), records them,
applies ``observation_start`` server-side, and answers with a hand-written JSON body.

The handler is driven by the project's own generic ``TierIngestor`` (discover, then
fetch per item, saving every returned document), so "emitted" means "handed to the
document store". ``today`` is fixed; the API key is a sentinel set in the environment
for the duration of one run only. No network, no ``data/``, no clock.
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# E402 below: these imports need src on sys.path, set just above.
from ingest.common.base import TierIngestor  # noqa: E402
from ingest.common.chunking import Chunker  # noqa: E402
from ingest.common.config import ChunkingSettings, DiscoveryConfig, EndpointConfig  # noqa: E402
from ingest.common.models import FetchResult  # noqa: E402
from ingest.usstats.handlers.fred import FredDailyHandler  # noqa: E402

SENTINEL = "SENTINEL-KEY-123"
KEY_ENV = "SPEC_FRED_API_KEY"          # deliberately not FRED_API_KEY: the handler
                                       # must read the name the config gives it
TODAY = date(2026, 10, 14)             # the spec's coverage-check date
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
REPO_ROOT = _ROOT


class FailStatus:
    """Response stand-in: the request fails with this HTTP status."""

    def __init__(self, status: int):
        self.status = status


class FakeFredClient:
    """Fake ``PoliteClient``: answers per ``series_id`` and records every request.

    ``responses`` maps series id to one of
      * a list of ``(date, value)`` string pairs -> a JSON observations body, filtered
        server-side by ``observation_start`` like the real API;
      * ``bytes`` -> that body verbatim (status 200);
      * ``FailStatus`` -> a non-200 response carrying a FRED-style error body.
    ``default`` answers series not in ``responses``.
    """

    def __init__(self, responses: dict, default=None):
        self.responses = responses
        self.default = default
        self.requests: list[dict] = []

    def _params(self, url: str, kwargs: dict) -> dict:
        params = {k: v[-1] for k, v in parse_qs(urlsplit(url).query,
                                                keep_blank_values=True).items()}
        extra = kwargs.get("params")
        if isinstance(extra, dict):
            params.update({str(k): str(v) for k, v in extra.items()})
        return params

    def get(self, url, *args, **kwargs):
        params = self._params(str(url), kwargs)
        self.requests.append({"url": str(url), "params": params})
        spec = self.responses.get(params.get("series_id"), self.default)
        if isinstance(spec, FailStatus):
            body = json.dumps({"error_code": spec.status,
                               "error_message": "Bad Request."}).encode()
            return FetchResult(url=str(url), status_code=spec.status, content=body,
                               content_type="application/json",
                               content_hash=hashlib.sha256(body).hexdigest())
        if isinstance(spec, bytes):
            body = spec
        else:
            start = params.get("observation_start", "")
            obs = [{"realtime_start": "2026-10-14", "realtime_end": "2026-10-14",
                    "date": d, "value": v}
                   for d, v in (spec or []) if not start or d >= start]
            body = json.dumps({"realtime_start": "2026-10-14",
                               "realtime_end": "2026-10-14",
                               "observation_start": start or "1600-01-01",
                               "units": "lin", "count": len(obs),
                               "observations": obs}).encode()
        return FetchResult(url=str(url), status_code=200, content=body,
                           content_type="application/json",
                           content_hash=hashlib.sha256(body).hexdigest())

    def head(self, url, *args, **kwargs):
        return 200

    def post_json(self, url, payload, *args, **kwargs):
        raise AssertionError("FRED observations are a GET endpoint")


class FakeCache:
    def is_processed(self, *args, **kwargs):
        return False                      # idempotency never hides output

    def mark_processed(self, *args, **kwargs):
        pass

    def get(self, *args, **kwargs):
        return None

    def put(self, *args, **kwargs):
        pass


class FakeRawStore:
    def __init__(self):
        self.names: list[str] = []

    def save(self, *args, **kwargs):
        self.names.extend(str(a) for a in args if isinstance(a, str))
        self.names.extend(str(v) for v in kwargs.values() if isinstance(v, str))
        return "raw/path"


class FakeDocStore:
    def __init__(self):
        self.docs: list = []

    def save(self, doc):
        self.docs.append(doc)
        return f"processed/{doc.doc_id}.json"


class FredIngestor(TierIngestor):
    """The project's generic driver with only the handler under test registered."""

    handler_registry = {"fred": FredDailyHandler}


def endpoint(series: list[tuple[str, str]], *, backfill_start: str = "2005-01-01",
             key_env: str = KEY_ENV) -> EndpointConfig:
    return EndpointConfig(
        id="fred_daily", handler="fred", title="FRED daily market series",
        publisher="Federal Reserve Bank of St. Louis", bucket="C",
        discovery=DiscoveryConfig(type="api", url=FRED_URL),
        content={"api_key_env": key_env, "backfill_start": backfill_start,
                 "series": [{"id": s, "units": u} for s, u in series]},
    )


def run(cfg: EndpointConfig, client: FakeFredClient, *, refresh: bool,
        today: date = TODAY, key_env: str | None = None):
    """Run one ingest of ``cfg`` with the sentinel key; return (docs, raw, client)."""
    raw, docs = FakeRawStore(), FakeDocStore()
    tier = types.SimpleNamespace(tier=3, name="U.S. Statistical & Policy Agencies",
                                 publisher="U.S. Government",
                                 licence="public_domain_us_gov", endpoints=[cfg])
    ctx = types.SimpleNamespace(
        tier=tier, client=client, cache=FakeCache(), raw_store=raw,
        doc_store=docs, chunker=Chunker(ChunkingSettings()), today=today,
        settings=types.SimpleNamespace(http=types.SimpleNamespace(user_agent="test")),
        refresh=refresh,
    )
    env = key_env or cfg.content.get("api_key_env") or KEY_ENV
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(env, SENTINEL)
        mp.delenv(f"{env}_FILE", raising=False)
        FredIngestor(ctx).run()
    return docs.docs, raw, client


def by_id(docs) -> dict:
    out: dict = {}
    for d in docs:
        assert d.doc_id not in out, f"duplicate document {d.doc_id}"
        out[d.doc_id] = d
    return out


def assert_rows(actual, expected):
    """Rows equal exactly, floats to rel=1e-12 (decimal string -> float round trip)."""
    assert isinstance(actual, list)
    assert len(actual) == len(expected), (actual, expected)
    for a, e in zip(actual, expected, strict=True):
        assert set(a) == set(e), (a, e)
        for k in e:
            if k == "value":
                assert a[k] == pytest.approx(e[k], rel=1e-12)
            else:
                assert a[k] == e[k], (k, a, e)


def row(series: str, d: str, v: float, units: str) -> dict:
    return {"source": "FRED", "series_code": series, "date": d, "value": v,
            "units": units}


def assert_no_key(docs, raw: FakeRawStore):
    for d in docs:
        assert SENTINEL not in d.model_dump_json(), d.doc_id
    for name in raw.names:
        assert SENTINEL not in name, name


@pytest.fixture(scope="session")
def fred():
    """Namespace of the helpers above, for the test modules."""
    return types.SimpleNamespace(
        SENTINEL=SENTINEL, KEY_ENV=KEY_ENV, TODAY=TODAY, REPO_ROOT=REPO_ROOT,
        FailStatus=FailStatus, FakeFredClient=FakeFredClient, endpoint=endpoint,
        run=run, by_id=by_id, assert_rows=assert_rows, row=row,
        assert_no_key=assert_no_key)
