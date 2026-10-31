"""Shared base for Tier 6A handlers (HEAD probe for URL-construction discovery)."""

from __future__ import annotations

from ..common.handler_base import BaseSourceHandler


class PowerHandler(BaseSourceHandler):
    """Base for the EIA / IEA / FERC / PJM endpoint handlers."""

    def _exists(self, url: str) -> bool:
        return self.ctx.client.head(url) == 200
