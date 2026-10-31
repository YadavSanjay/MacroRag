"""Per-endpoint Tier 4 handlers (Ingestion Design §7.1–§7.5).

``HANDLERS`` maps the ``handler`` id used in ``tier4_intl.yaml`` to its class.
"""

from __future__ import annotations

from ..common_base import IntlHandler
from .bis_annual import BisAnnualHandler
from .bis_quarterly import BisQuarterlyHandler
from .boj_outlook import BojOutlookHandler
from .imf_reports import ImfReportsHandler
from .imf_sdmx import ImfSdmxHandler

HANDLERS: dict[str, type[IntlHandler]] = {
    "bis_quarterly": BisQuarterlyHandler,
    "bis_annual": BisAnnualHandler,
    "imf_reports": ImfReportsHandler,
    "boj_outlook": BojOutlookHandler,
    "imf_sdmx": ImfSdmxHandler,
}

__all__ = [
    "HANDLERS", "IntlHandler", "BisQuarterlyHandler", "BisAnnualHandler",
    "ImfReportsHandler", "BojOutlookHandler", "ImfSdmxHandler",
]
