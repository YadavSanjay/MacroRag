"""Backup / restore the durable local state (Production Hardening §L).

Backup/restore was docs-only. This packages the on-disk state that a rebuild can't
cheaply regenerate into a single tar.gz and restores it:

  - the Qdrant on-disk store       (data/vectorstore/qdrant/)   [local mode only]
  - the time-series SQLite db      (data/vectorstore/timeseries.sqlite)
  - the index manifest             (data/vectorstore/manifest.json)  [local mode only;
                                    in aws mode it and the corpus are S3 objects]
  - eval baseline + history        (data/eval/)
  - the feedback log               (data/traces/feedback.jsonl)

Server-mode Qdrant (settings.qdrant.url set) is NOT dumped here — use Qdrant's own
snapshot API for that; a warning is printed.

Usage (PYTHONPATH=src):
    python scripts/backup_restore.py backup  data/backups/macrorag-2026-07-14.tar.gz
    python scripts/backup_restore.py restore data/backups/macrorag-2026-07-14.tar.gz [--force]
"""

from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


# ── pure, testable core ──────────────────────────────────────────────────────

def create_backup(items: list[Path], archive: Path, *, root: Path) -> list[str]:
    """Tar.gz every existing path in ``items`` (file or dir), stored relative to
    ``root``. Returns the archived member names. Missing items are skipped."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    stored: list[str] = []
    with tarfile.open(archive, "w:gz") as tar:
        for item in items:
            if not item.exists():
                continue
            name = str(item.relative_to(root)).replace("\\", "/")
            tar.add(item, arcname=name)
            stored.append(name)
    return stored


def restore_backup(archive: Path, root: Path, *, force: bool = False) -> list[str]:
    """Extract ``archive`` into ``root``. Refuses to overwrite an existing member
    unless ``force``. Returns the restored member names."""
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if not force:
            for m in members:
                target = root / m.name
                if target.exists():
                    raise FileExistsError(
                        f"{target} exists; pass --force to overwrite the restore target")
        # filter="data" (py3.12+) blocks path-escape members; fall back if unavailable
        try:
            tar.extractall(root, filter="data")
        except TypeError:  # pragma: no cover - older Python without the filter arg
            tar.extractall(root)
        return [m.name for m in members]


# ── CLI ──────────────────────────────────────────────────────────────────────

def _backup_items(settings) -> list[Path]:
    items = [settings.timeseries_path, settings.feedback_path,
             settings.eval_baseline_path, settings.eval_history_path]
    if getattr(settings, "storage_backend", "local") == "s3":
        # The live manifest and the processed corpus are S3 objects; a local
        # manifest.json on the instance would be stale, so it is not archived.
        print("note: storage backend is s3 — the processed corpus and manifest live in "
              "the S3 bucket and are not in this archive; rely on bucket versioning.")
    else:
        items.insert(1, settings.manifest_path)
    qdrant = settings.qdrant_path()
    if qdrant is not None:
        items.append(qdrant)
    elif settings.qdrant.url:
        print("note: Qdrant is in server mode (qdrant.url set) — not dumped here; "
              "use Qdrant's snapshot API for the vector store.")
    return items


def main(argv: list[str] | None = None) -> int:
    from utils.config import REPO_ROOT as R
    from utils.config import load_settings
    p = argparse.ArgumentParser(description="Backup/restore MacroRAG durable state")
    sub = p.add_subparsers(dest="cmd", required=True)
    pb = sub.add_parser("backup"); pb.add_argument("archive")
    pr = sub.add_parser("restore"); pr.add_argument("archive")
    pr.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    settings = load_settings()
    if args.cmd == "backup":
        stored = create_backup(_backup_items(settings), Path(args.archive), root=R)
        print(f"backed up {len(stored)} item(s) -> {args.archive}")
        for s in stored:
            print(f"  {s}")
    else:
        restored = restore_backup(Path(args.archive), R, force=args.force)
        print(f"restored {len(restored)} item(s) from {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
