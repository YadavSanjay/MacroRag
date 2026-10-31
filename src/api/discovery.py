"""Public discovery surface — the controlled vocabulary and shock-field schema.

An LLM-driven consumer (e.g. Faultline's ScenarioGen) needs to know the
*valid* facet tags before it can build ``/query`` filters, and the *valid* shock
fields (with units + sign conventions) before it can request or interpret a
synthesized shock vector. Both were previously reachable only via the
admin-gated ``/admin/vocabulary`` (tags) or not at all (shock schema, which lived
only inside the synthesis prompt).

These routes expose that reference data under the **normal request auth** (any
valid API key, no admin privilege), since it is non-sensitive catalog data
shared across the whole API. Read-only; no models or vector store touched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI

from utils.config import RagSettings
from utils.logging import get_logger

log = get_logger(__name__)


def vocabulary_view() -> dict[str, Any]:
    """Sorted controlled vocabulary (factor/entity tags, doc types) + alias map.
    Mirrors the admin view but is published for filter construction."""
    from ingest.common.config import load_vocabulary
    v = load_vocabulary()
    return {
        "factor_tags": sorted(v["factor_tags"]),
        "entity_tags": sorted(v["entity_tags"]),
        "doc_types": sorted(v["doc_types"]),
        "aliases": v["aliases"],
    }


def add_discovery_routes(
    app: FastAPI,
    *,
    settings: RagSettings,
    auth_dep: Callable[..., Any],
) -> None:
    """Register the public ``/vocabulary`` and ``/synthesis/schema`` endpoints."""

    @app.get("/vocabulary")
    def vocabulary(_: Any = Depends(auth_dep)):
        return vocabulary_view()

    @app.get("/synthesis/schema")
    def synthesis_schema(_: Any = Depends(auth_dep)):
        from synthesis import schema_dict
        return schema_dict()
