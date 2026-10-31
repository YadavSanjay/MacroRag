"""C1: the vocabulary gains REAL_YIELD_10Y and TERM_PREMIUM_10Y."""

from __future__ import annotations

import pytest

from api.models import validate_shock_fields
from synthesis.vocab import SHOCK_FIELDS, schema_dict

# oracle: spec C1 table.
NEW = {"REAL_YIELD_10Y": "real yield rises (10Y TIPS)",
       "TERM_PREMIUM_10Y": "term premium rises (ACM 10Y)"}
# The 17 fields of the contract before this spec (guide 05 §5).
OLD = ["UST_2Y", "UST_5Y", "UST_10Y", "UST_30Y", "UST", "IG_OAS", "HY_OAS", "MOVE",
       "MOVE_1Y", "MOVE_5Y", "MOVE_10Y", "MOVE_30Y", "VIX", "EQUITY_US", "USDTWD", "USDJPY",
       "XCCY_BASIS"]


@pytest.mark.criterion("C1")
def test_new_fields_specified_and_accepted():
    # oracle: spec C1 — units {bp}, default bp, the positive_means wording; the API
    # validator accepts them (case-insensitively, like every field).
    for name, means in NEW.items():
        assert name in SHOCK_FIELDS, name
        spec = SHOCK_FIELDS[name]
        assert set(spec.units) == {"bp"} and spec.preferred == "bp", name
        assert spec.positive_means == means, name
    assert validate_shock_fields(["real_yield_10y", "TERM_PREMIUM_10Y"]) == \
        ["REAL_YIELD_10Y", "TERM_PREMIUM_10Y"]


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_schema_lists_nineteen_and_old_seventeen_unchanged():
    # oracle: spec C1. Relation: the schema is the old 17 plus the 2 new, nothing else.
    names = {f["name"] for f in schema_dict()["fields"]}
    assert names == set(OLD) | set(NEW)
    assert set(SHOCK_FIELDS) == set(OLD) | set(NEW)
