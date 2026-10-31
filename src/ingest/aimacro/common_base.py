"""Shared base for Tier 6C handlers.

Thin specialisation of :class:`BaseSourceHandler`; the numeric-document and PDF
helpers it relies on already live on the base. Kept as a named class for a stable
registry type and a home for any 6C-specific behaviour.
"""

from __future__ import annotations

from ..common.handler_base import BaseSourceHandler


class AiMacroHandler(BaseSourceHandler):
    """Base for the GPR / EPU / BTOS / Stanford-HAI handlers."""
