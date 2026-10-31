"""Copy the local processed corpus into the S3 processed store (spec corpus-sync).

Seeds an AWS deployment with the curated local corpus, which a fresh ingest on the
instance cannot reproduce (FRED's rolling ICE OAS window, FOMC minutes that left
the feed). Then run a full ``index`` on the instance: it rebuilds the vectors, the
time-series store and the manifest from the copied documents.

Dry run by default; ``--apply`` writes. Never deletes from S3; a key whose bytes
differ is reported as a conflict and left alone unless ``--overwrite``. AWS
credentials come from the ambient chain (``AWS_PROFILE`` etc.), never from here.

Usage (PYTHONPATH=src):
    python scripts/sync_corpus.py --bucket <corpus-bucket>            # dry run
    python scripts/sync_corpus.py --bucket <corpus-bucket> --apply
    python scripts/sync_corpus.py --prefix tier3/fred_daily/ --apply  # one source
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from utils.config import load_settings  # noqa: E402
from utils.corpus_sync import sync_objects  # noqa: E402
from utils.objectstore import LocalObjectStore, build_object_store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bucket", default=os.environ.get("MACRORAG_S3_BUCKET"),
                   help="corpus bucket (default: MACRORAG_S3_BUCKET)")
    p.add_argument("--s3-prefix", default=os.environ.get("MACRORAG_S3_PREFIX", "corpus"),
                   help="corpus prefix in the bucket (default: MACRORAG_S3_PREFIX or 'corpus')")
    p.add_argument("--region", default=os.environ.get("MACRORAG_S3_REGION"))
    p.add_argument("--prefix", default="", help="only keys under this, e.g. tier3/fred_daily/")
    p.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    p.add_argument("--overwrite", action="store_true",
                   help="replace S3 objects whose bytes differ from the local copy")
    args = p.parse_args(argv)
    if not args.bucket:
        p.error("no bucket: pass --bucket or set MACRORAG_S3_BUCKET")

    settings = load_settings()
    src = LocalObjectStore(settings.processed_path)
    dst = build_object_store(backend="s3", local_root=settings.processed_path,
                             s3_subprefix="processed/ingest", s3_bucket=args.bucket,
                             s3_prefix=args.s3_prefix, s3_region=args.region)
    print(f"{src.location} -> {dst.location}"
          f"{' (prefix ' + args.prefix + ')' if args.prefix else ''}")
    counts = sync_objects(src, dst, prefix=args.prefix, dry_run=not args.apply,
                          overwrite=args.overwrite)
    print(f"{'' if args.apply else 'DRY-RUN: '}{counts}")
    if counts["conflict"]:
        print(f"{counts['conflict']} object(s) differ in S3 and were left alone; "
              "rerun with --overwrite to replace them with the local copy.")
    if args.apply and (counts["copied"] or counts["overwritten"]):
        print("next, on the instance: python -m pipelines.cli index   (full build)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
