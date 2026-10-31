"""Re-extract DFAST supervisory-scenario numerics from the Fed's companion CSVs
and retire the legacy PDF-table grid.

Two idempotent steps:
  1. run the ``dfast`` endpoint (``--refresh``) so the scenario CSVs are ingested
     as clean long-format time-series docs (PDFs are idempotent-skipped);
  2. strip the old grid-shape ``timeseries`` (rows carrying ``header``/``cells``)
     from the narrative ``dfast_scenarios`` PDF docs — those numbers now live in
     the ``dfast_scenario_data`` CSV docs. Narrative chunks are left untouched.

Usage:
    python scripts/reextract_dfast.py            # ingest CSVs + strip legacy grid
    python scripts/reextract_dfast.py --strip-only
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

from ingest import TIER_INGESTORS
from ingest.common import build_context, setup_logging
from ingest.common.config import load_common_settings
from ingest.common.storage import DocumentStore


def ingest_csvs() -> None:
    ctx = build_context("tier1_fed", refresh=True)
    try:
        report = TIER_INGESTORS["tier1_fed"](ctx).run(
            endpoint_ids=["dfast"], limit_per_endpoint=None)
    finally:
        ctx.client.close()
        ctx.cache.close()
    print(f"  ingest: {report}")


def strip_legacy_grid(store: DocumentStore | None = None) -> None:
    """Strip grid rows from stored DFAST docs; ``store`` defaults to the configured corpus."""
    store = store or DocumentStore(load_common_settings().storage)
    stripped = 0
    for doc in store.iter_documents("tier1/dfast/"):
        # grid rows are the old raw-table dump; long CSV rows have 'variable'
        legacy = [r for r in doc.timeseries if "cells" in r or "header" in r]
        if legacy and doc.doc_type != "dfast_scenario_data":
            doc.timeseries = [r for r in doc.timeseries if r not in legacy]
            store.save(doc)
            stripped += 1
            print(f"  stripped {len(legacy)} grid rows from {doc.doc_id}")
    print(f"  legacy-grid docs cleaned: {stripped}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strip-only", action="store_true",
                    help="skip CSV ingestion; only retire the legacy PDF grid")
    args = ap.parse_args(argv)
    setup_logging(None)
    if not args.strip_only:
        ingest_csvs()
    strip_legacy_grid()
    return 0


if __name__ == "__main__":
    sys.exit(main())
