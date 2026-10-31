"""Reusable Federal Register API client (Ingestion Design §5.2, §2.7).

The Federal Register REST API is the shared catalyst feed behind several tiers:
Treasury OISP rules (``agencies[]=investment-security``), BIS-Commerce export
rules (``agencies[]=industry-and-security-bureau``), and Presidential
Proclamations. This thin client builds the query, pages through results, and
normalises each document to a flat dict. No auth required.

API docs: https://www.federalregister.gov/developers/documentation/api/v1
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Optional
from urllib.parse import urlencode

from .http_client import PoliteClient
from .logging_utils import get_logger

log = get_logger(__name__)

BASE = "https://www.federalregister.gov/api/v1/documents.json"

# Fields requested explicitly so the list endpoint returns the rich document
# body URL + structured rule metadata (Ingestion Design §5.2 Stage B).
DEFAULT_FIELDS = [
    "document_number", "title", "abstract", "type", "publication_date",
    "effective_on", "dates", "html_url", "pdf_url", "body_html_url",
    "raw_text_url", "json_url", "agencies", "agency_names", "topics",
    "cfr_references",
]


def build_url(
    *,
    conditions: dict[str, Any],
    fields: Iterable[str] | None = None,
    per_page: int = 100,
    order: str = "newest",
    page: int = 1,
) -> str:
    """Construct a documents.json query URL from a conditions mapping.

    ``conditions`` keys map to ``conditions[<key>]``; list values expand to the
    PHP-style ``conditions[<key>][]=v`` repeated-param form the API expects.
    """
    params: list[tuple[str, str]] = []
    for key, value in conditions.items():
        if isinstance(value, (list, tuple)):
            for v in value:
                params.append((f"conditions[{key}][]", str(v)))
        else:
            params.append((f"conditions[{key}]", str(value)))
    for f in (fields if fields is not None else DEFAULT_FIELDS):
        params.append(("fields[]", f))
    params.append(("per_page", str(per_page)))
    params.append(("order", order))
    params.append(("page", str(page)))
    return f"{BASE}?{urlencode(params)}"


def search(
    client: PoliteClient,
    *,
    conditions: dict[str, Any],
    fields: Iterable[str] | None = None,
    per_page: int = 100,
    order: str = "newest",
    max_pages: int = 1,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Run a Federal Register search and return the result documents."""
    out: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = build_url(conditions=conditions, fields=fields,
                        per_page=per_page, order=order, page=page)
        payload = client.get_json(url, use_cache=use_cache)
        if not payload:
            break
        results = payload.get("results", []) or []
        out.extend(results)
        if len(results) < per_page:
            break  # last page
    log.info("Federal Register: %d document(s) for conditions=%s",
             len(out), conditions)
    return out


def pre_text(content: bytes) -> str:
    """Extract the rule body from a Federal Register ``raw_text_url`` payload.

    That endpoint returns the full document text wrapped in ``<pre>``; this
    returns the inner text (falling back to the whole document text).
    """
    from bs4 import BeautifulSoup  # local import keeps the module import-light

    soup = BeautifulSoup(content, "lxml")
    pre = soup.find("pre")
    text = (pre.get_text("\n") if pre else soup.get_text("\n"))
    import re

    return re.sub(r"\n{3,}", "\n\n", text).strip()
