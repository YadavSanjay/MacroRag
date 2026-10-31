"""Shared base class for all Fed endpoint handlers.

The generic mechanics (discovery/content fetch, metadata + document assembly)
now live in :class:`ingest.common.handler_base.BaseSourceHandler`. ``FedHandler``
is the Tier-1 specialisation; it currently needs no Fed-specific overrides, but
keeping the class gives the handlers a stable, semantically-named base and a
home for any future Fed-only behaviour.
"""

from __future__ import annotations

from ..common.handler_base import BaseSourceHandler


class FedHandler(BaseSourceHandler):
    """Base for the five Federal Reserve endpoint handlers."""
