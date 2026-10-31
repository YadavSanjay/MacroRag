"""Re-apply an endpoint's configured tags to its already-processed documents.

Chunk tags are stamped at ingest from the tier config; after changing an endpoint's
``factor_tags`` / ``entity_tags``, run this so stored documents match, then rebuild the
index (``python -m pipelines.cli index``; ``--incremental`` does not see tag-only
changes). Works on the local filesystem or S3 through the object store
(``MACRORAG_DEPLOY_MODE``). Spec: docs/specs/retag-endpoint.md.

Usage:
    python scripts/retag_endpoint.py --tier tier1_fed --endpoints press_monetary,speeches --dry-run
    python scripts/retag_endpoint.py --tier tier1_fed --endpoints press_monetary,speeches
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

from ingest.common.config import load_common_settings, load_tier_config
from ingest.common.retag import retag_documents
from utils.objectstore import object_store_from_env


def main(argv: list[str] | None = None) -> int:
    """Retag the named endpoints' processed documents; print counts per endpoint."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--tier", required=True, help="tier config stem, e.g. tier1_fed")
    p.add_argument("--endpoints", required=True, help="comma-separated endpoint ids")
    p.add_argument("--dry-run", action="store_true", help="count only; write nothing")
    args = p.parse_args(argv)

    tier = load_tier_config(args.tier)
    by_id = {ep.id: ep for ep in tier.endpoints}
    wanted = [e.strip() for e in args.endpoints.split(",") if e.strip()]
    unknown = [e for e in wanted if e not in by_id]
    if unknown:
        p.error(f"unknown endpoint(s) in {args.tier}: {unknown}")
    settings = load_common_settings()
    store = object_store_from_env(settings.storage.processed_dir, "processed/ingest")
    for eid in wanted:
        ep = by_id[eid]
        counts = retag_documents(store, tier=tier.tier, source_id=eid,
                                 factor_tags=list(ep.factor_tags),
                                 entity_tags=list(ep.entity_tags), dry_run=args.dry_run)
        mode = "DRY-RUN " if args.dry_run else ""
        print(f"{mode}{eid}: {counts}  factor_tags={list(ep.factor_tags)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
