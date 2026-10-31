"""Command-line entry point for on-demand / weekly macro ingestion.

Examples
--------
    # List the Tier 1 (Fed) endpoints and their cadence
    python -m ingest --tier tier1_fed --list

    # Run all enabled Fed endpoints
    python -m ingest --tier tier1_fed

    # Run only the FOMC feed, at most 3 new documents (handy for a smoke test)
    python -m ingest --tier tier1_fed --endpoints press_monetary --limit 3
"""

from __future__ import annotations

import argparse
import sys

from . import TIER_INGESTORS
from .common import build_context, load_tier_config, setup_logging
from .common.logging_utils import get_logger

log = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ingest",
        description="Macro RAG ingestion (on-demand / weekly).",
    )
    p.add_argument("--tier", default="tier1_fed",
                   help="tier config stem under configs/ingest/ (default: tier1_fed)")
    p.add_argument("--endpoints", default=None,
                   help="comma-separated endpoint ids to run (default: all enabled)")
    p.add_argument("--limit", type=int, default=None,
                   help="max new documents to fetch per endpoint (smoke testing)")
    p.add_argument("--refresh", action="store_true",
                   help="bypass discovery cache and re-list everything available "
                        "(fetch latest; content idempotency still dedups)")
    p.add_argument("--list", action="store_true",
                   help="list endpoints in the tier config and exit")
    p.add_argument("--log-level", default=None, help="override LOG_LEVEL")
    return p


def _list_endpoints(tier_name: str) -> int:
    tier = load_tier_config(tier_name)
    print(f"Tier {tier.tier} — {tier.name} ({tier.publisher}, {tier.licence})")
    print(f"  base_url: {tier.base_url}")
    for ep in tier.endpoints:
        sched = ep.release_schedule.frequency if ep.release_schedule else "n/a"
        flag = "" if ep.enabled else "  [disabled]"
        print(f"  • {ep.id:<22} bucket={ep.bucket} poll={ep.poll_cadence:<8} "
              f"release={sched}{flag}")
        print(f"      {ep.title}")
        print(f"      discovery[{ep.discovery.type}]: {ep.discovery.url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(args.log_level)

    if args.tier not in TIER_INGESTORS:
        log.error("unknown tier '%s'; known: %s", args.tier,
                  ", ".join(sorted(TIER_INGESTORS)))
        return 2

    if args.list:
        return _list_endpoints(args.tier)

    endpoint_ids = (
        [e.strip() for e in args.endpoints.split(",") if e.strip()]
        if args.endpoints else None
    )

    ctx = build_context(args.tier, refresh=args.refresh)
    try:
        ingestor = TIER_INGESTORS[args.tier](ctx)
        report = ingestor.run(endpoint_ids=endpoint_ids,
                              limit_per_endpoint=args.limit)
    finally:
        ctx.client.close()
        ctx.cache.close()

    print(f"\n{report}")
    return 1 if report.errors and report.parsed == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
