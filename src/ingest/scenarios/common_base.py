"""Shared base for Tier 7 (scenario / stress-test) handlers.

Thin specialisation of :class:`BaseSourceHandler`; the PDF and document-assembly
helpers it relies on already live on the base. Kept as a named class for a stable
registry type and a home for any tier-7-specific behaviour.
"""

from __future__ import annotations

from ..common.handler_base import BaseSourceHandler


class ScenariosHandler(BaseSourceHandler):
    """Base for the cross-asset stress & geopolitical-scenario handlers."""
