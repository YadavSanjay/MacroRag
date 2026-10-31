"""Controlled shock-field vocabulary for the AITrade scenario-synthesis contract
(MacroRAG_synthesis_endpoint_spec.md §4). Field names, allowed units, and sign
conventions MUST match the spec exactly — these are the canonical risk-factor
drivers the AITrade stress engine consumes. Unknown fields/units are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    units: frozenset[str]      # allowed units for this field
    preferred: str             # the unit MacroRAG should emit
    positive_means: str        # documented sign convention


# field -> spec.  POSITIVE sign convention per §4.
SHOCK_FIELDS: dict[str, FieldSpec] = {
    "UST_2Y":  FieldSpec(frozenset({"bp"}), "bp", "yields rise"),
    "UST_5Y":  FieldSpec(frozenset({"bp"}), "bp", "yields rise"),
    "UST_10Y": FieldSpec(frozenset({"bp"}), "bp", "yields rise"),
    "UST_30Y": FieldSpec(frozenset({"bp"}), "bp", "yields rise"),
    "UST":     FieldSpec(frozenset({"bp"}), "bp", "yields rise (parallel shift)"),
    "IG_OAS":  FieldSpec(frozenset({"bp"}), "bp", "spreads widen"),
    "HY_OAS":  FieldSpec(frozenset({"bp"}), "bp", "spreads widen"),
    "MOVE":    FieldSpec(frozenset({"rel", "pts"}), "rel", "rates vol rises (aggregate/parallel)"),
    # Rates-vol TERM STRUCTURE by swaption expiry — lets the swaption/cap book see
    # a vol curve, not just the single MOVE scalar. Same units/sign as MOVE.
    "MOVE_1Y":  FieldSpec(frozenset({"rel", "pts"}), "rel", "rates vol rises (~1Y expiry)"),
    "MOVE_5Y":  FieldSpec(frozenset({"rel", "pts"}), "rel", "rates vol rises (~5Y expiry)"),
    "MOVE_10Y": FieldSpec(frozenset({"rel", "pts"}), "rel", "rates vol rises (~10Y expiry)"),
    "MOVE_30Y": FieldSpec(frozenset({"rel", "pts"}), "rel", "rates vol rises (~30Y expiry)"),
    "VIX":     FieldSpec(frozenset({"pts"}), "pts", "equity vol rises"),
    # Broad US equity LEVEL (not vol). The macro envelope previously described
    # rates/credit/vol/FX but said nothing about the equity leg, which is where an
    # AI-themed book's P&L actually sits. Grounded in the DFAST `dow_jones` path
    # (3 scenarios) + 156 quarters of realized history, so it is backtestable on
    # the same machinery as the rest. Single-name and sector dispersion remain
    # out of scope — that is AITrade's bottom-up methodology, not this contract.
    "EQUITY_US": FieldSpec(frozenset({"pct"}), "pct", "equity index rises"),
    "USDTWD":  FieldSpec(frozenset({"pct"}), "pct", "TWD depreciates"),
    "USDJPY":  FieldSpec(frozenset({"pct"}), "pct", "JPY depreciates"),
    "XCCY_BASIS": FieldSpec(frozenset({"bp"}), "bp", "—"),
    # Rate decomposition (spec vocab-rates-decomp): measured on the market-episode
    # cards from FRED DFII10 and the NY Fed ACM model (faultline's tp:ACMTP10), not
    # Kim-Wright.
    "REAL_YIELD_10Y": FieldSpec(frozenset({"bp"}), "bp", "real yield rises (10Y TIPS)"),
    "TERM_PREMIUM_10Y": FieldSpec(frozenset({"bp"}), "bp", "term premium rises (ACM 10Y)"),
}


def is_valid_field(name: str) -> bool:
    return name in SHOCK_FIELDS


def is_valid_unit(name: str, unit: str) -> bool:
    spec = SHOCK_FIELDS.get(name)
    return spec is not None and unit in spec.units


def field_reference() -> str:
    """A compact field/unit/sign table for the synthesis prompt."""
    lines = ["field | unit(s) | positive sign means"]
    for name, spec in SHOCK_FIELDS.items():
        lines.append(f"{name} | {'/'.join(sorted(spec.units))} | {spec.positive_means}")
    return "\n".join(lines)


def schema_dict() -> dict:
    """Machine-readable shock-field catalog for the public discovery endpoint:
    every valid field with its allowed units, the preferred (emitted) unit, and
    the documented positive-sign convention. Lets an LLM consumer construct and
    interpret shock vectors without scraping the prompt or guessing field names."""
    return {
        "fields": [
            {
                "name": name,
                "units": sorted(spec.units),
                "preferred_unit": spec.preferred,
                "positive_means": spec.positive_means,
            }
            for name, spec in SHOCK_FIELDS.items()
        ],
        "grounding_modes": {
            "direct": "evidence directly quantifies this factor for the scenario",
            "analog": "proxy anchored to the closest historical analog / official "
                      "adverse-scenario path (still dated + cited); down-weight vs direct",
        },
    }


# Factor tags marking a quantified scenario study (regulator adverse paths, official
# event studies). Synthesize lets these past heuristic entity tags: they are the
# magnitude anchors the synthesize prompt points to, whatever entity they cover.
SCENARIO_STUDY_TAGS: tuple[str, ...] = ("cross_asset_scenario", "stress_scenario")


# Currencies an analog FX shock may be anchored on, per field (synth-analog-quality):
# Asian EM peers for TWD, the other funding/safe-haven currency for JPY. An analog
# rationale naming only other currencies (e.g. USD/UAH for TWD) is flagged.
FX_PROXY_ALLOWLIST: dict[str, frozenset[str]] = {
    "USDTWD": frozenset({"TWD", "KRW", "CNY", "CNH", "SGD", "HKD", "MYR", "THB", "PHP",
                         "IDR", "INR"}),
    "USDJPY": frozenset({"JPY", "CHF"}),
}
# Currency codes recognised in a rationale: the allowlists plus common others.
FX_CODES: frozenset[str] = frozenset().union(*FX_PROXY_ALLOWLIST.values()) | frozenset({
    "USD", "EUR", "GBP", "AUD", "NZD", "CAD", "NOK", "SEK", "MXN", "BRL", "ZAR", "TRY",
    "RUB", "UAH", "PLN", "HUF", "CZK", "ILS"})


# Minimum horizon (days) a move from these document types can describe
# (synth-analog-horizon): supervisory scenarios are quarterly or annual paths, so
# none of their numbers is a move over a few days.
DOC_TYPE_MIN_HORIZON_DAYS: dict[str, int] = {
    "dfast_scenarios": 90,
    "dfast_scenario_data": 90,
    "dfast_results": 90,
    "eba_esrb_adverse_scenario": 365,
}
