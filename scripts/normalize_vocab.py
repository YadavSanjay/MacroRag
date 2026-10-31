"""Normalize the controlled vocabulary on already-persisted ParsedDocuments.

For every processed JSON:
  * canonicalize each chunk's ``factor_tags`` / ``entity_tags`` against
    ``vocabulary.yaml`` (alias map: AI->ai, China->china, ai_investment->ai_capex,
    power_constraint->data_center_power);
  * keep the chunk-level ``doc_type`` in sync with the document;
  * reclassify a document whose ``doc_type == "unknown"`` by re-running the tier's
    ``doc_type_rules`` against its title (fixes the SLOOS / discount-rate-minutes
    regex misses).

Idempotent — re-run anytime. Pairs with the load-time enforcement in config.py
so existing data and future downloads share one vocabulary.

Usage:
    python scripts/normalize_vocab.py            # rewrite in place
    python scripts/normalize_vocab.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

sys.path.insert(0, "src")

from ingest.common import feeds
from ingest.common.config import (
    load_common_settings,
    load_tier_config,
    load_vocabulary,
    normalize_tags,
)
from ingest.common.storage import DocumentStore

TIER_CONFIGS = ["tier1_fed", "tier2_treasury", "tier3_usstats", "tier4_intl",
                "tier5_house", "tier6a_power", "tier6b_chips", "tier6c_aimacro"]


def build_endpoint_index() -> dict:
    idx = {}
    for name in TIER_CONFIGS:
        for ep in load_tier_config(name).endpoints:
            idx[ep.id] = ep
    return idx


def main(argv: list[str] | None = None, *, store: DocumentStore | None = None) -> int:
    """Normalize every stored document; ``store`` defaults to the configured corpus."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    settings = load_common_settings()
    store = store or DocumentStore(settings.storage)
    vocab = load_vocabulary()
    endpoints = build_endpoint_index()

    tag_changed = reclassified = 0
    unknown_remaining = []
    dt_audit = Counter()

    for doc in store.iter_documents():
        changed = False

        # 1) reclassify unknown doc_type via the tier rules (title-based)
        if doc.doc_type == "unknown":
            ep = endpoints.get(doc.source_id)
            if ep and ep.doc_type_rules:
                new_type, _, _ = feeds.classify(doc.title, ep.doc_type_rules)
                if new_type != "unknown":
                    doc.doc_type = new_type
                    reclassified += 1
                    changed = True

        # 2) canonicalize tags on every chunk; keep chunk doc_type in sync
        doc_tag_changed = False
        for ch in doc.chunks:
            m = ch.metadata
            nf = normalize_tags(m.factor_tags, "factor_tags", context=doc.source_id)
            ne = normalize_tags(m.entity_tags, "entity_tags", context=doc.source_id)
            if nf != m.factor_tags or ne != m.entity_tags or m.doc_type != doc.doc_type:
                m.factor_tags, m.entity_tags, m.doc_type = nf, ne, doc.doc_type
                doc_tag_changed = True
        if doc_tag_changed:
            tag_changed += 1
            changed = True

        if doc.doc_type == "unknown":
            unknown_remaining.append((doc.source_id, doc.title[:50]))
        dt_audit[doc.doc_type] += 1

        if changed and not args.dry_run:
            store.save(doc)

    # doc_type registry audit
    unregistered = sorted(dt for dt in dt_audit if dt not in vocab["doc_types"])
    print(f"\n{'DRY-RUN: ' if args.dry_run else ''}docs with tag changes: {tag_changed} | "
          f"reclassified unknown->known: {reclassified}")
    print(f"doc_types still 'unknown': {len(unknown_remaining)}")
    for s, t in unknown_remaining:
        print(f"    {s}: {t!r}")
    print(f"doc_types NOT in registry: {unregistered or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
