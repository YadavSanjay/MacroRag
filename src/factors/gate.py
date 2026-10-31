"""Citation/provenance GATE for discovered factors (mirrors synthesis/gate.py).

Every emitted factor must cite ≥ ``min_chunks`` chunk_ids that are present in the
response chunks[] **with a populated release_date**, so the consumer always has a
dated source to inspect. Factors that fail are dropped, not guessed.
"""

from __future__ import annotations

from utils.logging import get_logger
from utils.schema import RetrievedChunk

from .schema import RelevantFactor

log = get_logger(__name__)


def gate_factors(factors: list[RelevantFactor],
                 chunks: list[RetrievedChunk],
                 *, min_chunks: int = 1) -> list[RelevantFactor]:
    dated_ids = {c.chunk_id for c in chunks if c.payload.get("release_date")}
    kept: list[RelevantFactor] = []
    for f in factors:
        valid = [c for c in f.chunk_ids if c in dated_ids]
        if len(valid) < min_chunks:
            log.info("gate: dropping factor %s — %d/%d cited chunks present & dated",
                     f.factor_tag, len(valid), len(f.chunk_ids))
            continue
        f.chunk_ids = valid
        kept.append(f)
    return kept
