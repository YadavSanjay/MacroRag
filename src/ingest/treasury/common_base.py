"""Shared base for all Treasury endpoint handlers.

Adds the Treasury-specific body-cleaning hook (strip the FREEDOM250 banner and
the .gov security boilerplate, §5.4) on top of the generic
:class:`ingest.common.handler_base.BaseSourceHandler`.
"""

from __future__ import annotations

from ..common.handler_base import BaseSourceHandler
from .parsers import strip_treasury_boilerplate


class TreasuryHandler(BaseSourceHandler):
    """Base for the three U.S. Treasury endpoint handlers."""

    def clean_body(self, text: str) -> str:
        return strip_treasury_boilerplate(text)
