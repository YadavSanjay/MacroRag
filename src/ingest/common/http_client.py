"""Polite synchronous HTTP client (Ingestion Design §2.8).

Implements the cross-cutting fetch discipline shared by every tier:

* descriptive ``User-Agent`` on every request (the Fed silently rate-limits anon);
* per-host rate limiting (default 1 req/sec; tier configs may tighten);
* retry/backoff on 429 + 5xx, honouring ``Retry-After``;
* conditional GET using cached ``ETag`` / ``Last-Modified`` -> 304 short-circuit;
* SHA-256 body hashing so callers can detect "nothing changed" cheaply.

Sync (httpx.Client) is deliberate: ingestion is an on-demand/weekly batch job,
not a latency-sensitive service, so straight-line code beats async complexity.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .cache import FetchCache
from .config import HttpSettings
from .logging_utils import get_logger
from .models import FetchResult

log = get_logger(__name__)


class _RetryableStatus(Exception):
    """Raised internally to trigger a tenacity retry on 429 / 5xx."""

    def __init__(self, status: int, retry_after: float = 0.0):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"retryable HTTP {status}")


class PoliteClient:
    """A throttled, caching, retrying httpx wrapper. Use as a context manager."""

    def __init__(self, settings: HttpSettings, cache: FetchCache | None = None):
        self.settings = settings
        self.cache = cache
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent},
            timeout=settings.timeout_seconds,
            follow_redirects=True,
        )
        self._lock = threading.Lock()
        self._last_request_at: dict[str, float] = {}  # host -> monotonic ts
        self._min_interval = 1.0 / max(settings.rate_limit_per_sec, 0.01)

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ── throttling ───────────────────────────────────────────────────────────

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        with self._lock:
            last = self._last_request_at.get(host, 0.0)
            wait = self._min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at[host] = time.monotonic()

    # ── public API ───────────────────────────────────────────────────────────

    def get(self, url: str, *, use_cache: bool = True,
            extra_headers: dict | None = None) -> FetchResult:
        """GET ``url`` with throttling, retries and conditional-GET caching.

        ``extra_headers`` are merged into the request (e.g. an ``Accept`` header
        for SDMX content negotiation). Returns a :class:`FetchResult`;
        ``status_code == 304`` means cached validators matched (empty ``content``).
        """

        @retry(
            retry=retry_if_exception_type(
                (_RetryableStatus, httpx.TransportError, httpx.TimeoutException)
            ),
            stop=stop_after_attempt(self.settings.max_retries),
            wait=wait_exponential(multiplier=self.settings.backoff_factor, max=30),
            reraise=True,
        )
        def _do() -> FetchResult:
            self._throttle(url)
            headers: dict[str, str] = dict(extra_headers or {})
            entry = self.cache.get(url) if (use_cache and self.cache) else None
            if entry:
                if entry.etag:
                    headers["If-None-Match"] = entry.etag
                if entry.last_modified:
                    headers["If-Modified-Since"] = entry.last_modified

            resp = self._client.get(url, headers=headers)

            if resp.status_code in self.settings.retry_statuses:
                retry_after = float(resp.headers.get("Retry-After", 0) or 0)
                if retry_after:
                    time.sleep(min(retry_after, 30))
                raise _RetryableStatus(resp.status_code, retry_after)

            if resp.status_code == 304:
                log.debug("304 Not Modified: %s", url)
                return FetchResult(url=url, status_code=304, from_cache=True,
                                   etag=entry.etag if entry else None)

            resp.raise_for_status()
            body = resp.content
            digest = hashlib.sha256(body).hexdigest()
            result = FetchResult(
                url=url,
                status_code=resp.status_code,
                content=body,
                content_type=resp.headers.get("Content-Type", ""),
                etag=resp.headers.get("ETag"),
                last_modified=resp.headers.get("Last-Modified"),
                content_hash=digest,
            )
            if use_cache and self.cache:
                self.cache.put(
                    url,
                    etag=result.etag,
                    last_modified=result.last_modified,
                    content_hash=digest,
                )
            return result

        return _do()

    def post_json(self, url: str, payload: dict) -> FetchResult:
        """Throttled, retrying POST with a JSON body (the BLS API shape).

        Caching is not applicable (no conditional-GET semantics on POST), so the
        body hash is still computed for downstream idempotency checks.
        """

        @retry(
            retry=retry_if_exception_type(
                (_RetryableStatus, httpx.TransportError, httpx.TimeoutException)
            ),
            stop=stop_after_attempt(self.settings.max_retries),
            wait=wait_exponential(multiplier=self.settings.backoff_factor, max=30),
            reraise=True,
        )
        def _do() -> FetchResult:
            self._throttle(url)
            resp = self._client.post(url, json=payload)
            if resp.status_code in self.settings.retry_statuses:
                retry_after = float(resp.headers.get("Retry-After", 0) or 0)
                if retry_after:
                    time.sleep(min(retry_after, 30))
                raise _RetryableStatus(resp.status_code, retry_after)
            resp.raise_for_status()
            body = resp.content
            return FetchResult(
                url=url,
                status_code=resp.status_code,
                content=body,
                content_type=resp.headers.get("Content-Type", ""),
                content_hash=hashlib.sha256(body).hexdigest(),
            )

        return _do()

    def head(self, url: str) -> int:
        """Throttled HEAD request; returns the status code (0 on failure).

        Used to probe predictable-URL resources (e.g. the TIC monthly press
        notice) before committing to a full GET.
        """
        self._throttle(url)
        try:
            resp = self._client.head(url)
            return resp.status_code
        except httpx.HTTPError as exc:
            log.debug("HEAD failed for %s: %s", url, exc)
            return 0

    def get_json(self, url: str, *, use_cache: bool = True):
        """GET ``url`` and parse the body as JSON. Returns ``None`` on 304."""
        import json

        res = self.get(url, use_cache=use_cache)
        if res.not_modified or not res.ok:
            return None
        return json.loads(res.content)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
