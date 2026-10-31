"""Shared base for Tier 6B handlers.

The HTTP-then-headless fallback fetch (for hosts that 403 a normal request, e.g.
tsmc.com) now lives on :class:`BaseSourceHandler._fetch_or_render`; this stays as
a named base for the registry and any future 6B-specific behaviour.
"""

from __future__ import annotations

from ..common.handler_base import BaseSourceHandler


class ChipsHandler(BaseSourceHandler):
    """Base for the Federal Register / Entity List / TSMC / SIA handlers."""
