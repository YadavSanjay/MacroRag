"""Citation/provenance GATE for the shock vector (spec §5, MacroRAG side).

We enforce the parts we own: every field must cite ≥ ``min_chunks`` chunk_ids,
and every cited chunk must be present in the response chunks[] **with a populated
release_date** (so AITrade's freshness check has a date to work with). Fields that
fail are dropped. Freshness itself (age vs valuation_date) is AITrade's call.
"""

from __future__ import annotations

from utils.logging import get_logger
from utils.schema import RetrievedChunk

from .diagnostics import SynthesisDiagnostics
from .schema import ShockField

log = get_logger(__name__)


def gate_shock_vector(fields: dict[str, ShockField],
                      chunks: list[RetrievedChunk],
                      *, min_chunks: int = 1,
                      diag: SynthesisDiagnostics | None = None
                      ) -> dict[str, ShockField]:
    dated_ids = {c.chunk_id for c in chunks if c.payload.get("release_date")}
    kept: dict[str, ShockField] = {}
    for name, f in fields.items():
        valid = [c for c in f.chunk_ids if c in dated_ids]
        if len(valid) < min_chunks:
            log.info("gate: dropping %s — %d/%d cited chunks present & dated",
                     name, len(valid), len(f.chunk_ids))
            if diag is not None:
                diag.drop(name, "gate", "no_dated_citation",
                          cited=len(f.chunk_ids), valid=len(valid))
            continue
        f.chunk_ids = valid
        kept[name] = f
    return kept
