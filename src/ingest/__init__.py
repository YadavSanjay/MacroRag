"""Macro RAG ingestion package.

On-demand / weekly batch ingestion for the macro stress-testing corpus. The
design splits tier-agnostic machinery (:mod:`ingest.common`) from per-tier
implementations (:mod:`ingest.fed`, ...). ``TIER_INGESTORS`` maps a tier config
name (the YAML stem under ``configs/ingest/``) to its driver class so the CLI
can stay generic as new tiers land.
"""

from __future__ import annotations

from .aimacro import AiMacroIngestor
from .chips import ChipsIngestor
from .common.base import TierIngestor
from .fed import FedIngestor
from .house import HouseIngestor
from .intl import IntlIngestor
from .power import PowerIngestor
from .scenarios import ScenariosIngestor
from .treasury import TreasuryIngestor
from .usstats import UsStatsIngestor

#: config-stem -> ingestor class. Tiers 1-6 plus the Tier-7 scenario layer.
TIER_INGESTORS: dict[str, type[TierIngestor]] = {
    "tier1_fed": FedIngestor,
    "tier2_treasury": TreasuryIngestor,
    "tier3_usstats": UsStatsIngestor,
    "tier4_intl": IntlIngestor,
    "tier5_house": HouseIngestor,
    "tier6a_power": PowerIngestor,
    "tier6b_chips": ChipsIngestor,
    "tier6c_aimacro": AiMacroIngestor,
    "tier7_scenarios": ScenariosIngestor,
}

__all__ = [
    "TIER_INGESTORS", "FedIngestor", "TreasuryIngestor", "UsStatsIngestor",
    "IntlIngestor", "HouseIngestor", "PowerIngestor", "ChipsIngestor",
    "AiMacroIngestor", "ScenariosIngestor", "TierIngestor",
]
