"""Re-extract SEP (Summary of Economic Projections) numerics from the Fed's HTML
projections page, replacing the legacy mangled PDF-table grid.

Operates directly on the persisted ``sep_projections`` docs (cache/RSS
independent): for each, derive the projections-page URL from the release date
(``fomcprojtabl{YYYYMMDD}.htm``), parse its summary table into clean long rows,
and overwrite the doc's ``timeseries`` (narrative chunks untouched). Future
ingests use the same path via the press_monetary handler.

Idempotent. Usage:
    python scripts/reextract_sep.py            # rewrite in place
    python scripts/reextract_sep.py --dry-run
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

import httpx

from ingest.common import html_extract
from ingest.common.config import load_common_settings
from ingest.common.storage import DocumentStore
from ingest.fed import sep_projections

PROJ_URL = "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl{stamp}.htm"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    settings = load_common_settings()
    store = DocumentStore(settings.storage)
    client = httpx.Client(headers={"User-Agent": settings.http.user_agent},
                          timeout=30, follow_redirects=True)

    updated = 0
    try:
        for doc in store.iter_documents("tier1/press_monetary/"):
            if doc.doc_type != "sep_projections" or doc.release_date is None:
                continue
            stamp = doc.release_date.strftime("%Y%m%d")
            url = PROJ_URL.format(stamp=stamp)
            r = client.get(url)
            if r.status_code != 200:
                print(f"  {doc.doc_id}: projections page {r.status_code} ({url}); skipped")
                continue
            grids = html_extract.extract_tables(r.content)
            rows = sep_projections.parse_projection_table(
                grids[0], source=f"SEP_{doc.release_date:%Y-%m}",
                release=doc.release_date) if grids else []
            if not rows:
                print(f"  {doc.doc_id}: no rows parsed; skipped")
                continue
            before = len(doc.timeseries)
            doc.timeseries = rows
            print(f"  {doc.doc_id}: ts {before} -> {len(rows)} long rows")
            if not args.dry_run:
                store.save(doc)
            updated += 1
    finally:
        client.close()

    print(f"\n{'DRY-RUN: ' if args.dry_run else ''}SEP docs re-extracted: {updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
