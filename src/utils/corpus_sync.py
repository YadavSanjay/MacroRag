"""Copy objects from one :class:`~utils.objectstore.ObjectStore` to another.

Seeds the AWS (S3) processed corpus from the local one, so a full ``index`` on the
instance reproduces the curated local corpus (spec ``corpus-sync``). A sync never
deletes from the destination and never writes the source.
"""

from __future__ import annotations

from utils.objectstore import ObjectStore


def sync_objects(src: ObjectStore, dst: ObjectStore, *, prefix: str = "",
                 dry_run: bool = False, overwrite: bool = False) -> dict[str, int]:
    """Copy every ``src`` key under ``prefix`` that ``dst`` lacks or holds differently.

    Args:
        src: The store to read from; never written.
        dst: The store to write to; never deleted from.
        prefix: Key prefix to restrict the sync to; empty for every key.
        dry_run: Count what would happen, write nothing.
        overwrite: Replace a ``dst`` key whose bytes differ from ``src``.

    Returns:
        Counts keyed ``copied``, ``identical``, ``conflict``, ``overwritten`` and
        ``dst_only``.

    Raises:
        RuntimeError: A ``dst`` key is listed but its bytes cannot be read.
    """
    counts = dict.fromkeys(("copied", "identical", "conflict", "overwritten", "dst_only"), 0)
    src_keys = sorted(src.iter_keys(prefix))
    dst_keys = set(dst.iter_keys(prefix))
    for key in src_keys:
        data = src.get_bytes(key)
        if data is None:                 # vanished between listing and read
            continue
        have = dst.get_bytes(key)
        if have is None and key in dst_keys:
            # S3ObjectStore.get_bytes maps every error (AccessDenied included) to None;
            # a listed key that cannot be read must not be taken as absent and replaced.
            raise RuntimeError(f"{dst.location}: {key} is listed but unreadable")
        if have is None:
            outcome = "copied"
        elif have == data:
            counts["identical"] += 1
            continue
        elif overwrite:
            outcome = "overwritten"
        else:
            counts["conflict"] += 1
            continue
        if not dry_run:
            dst.put_bytes(key, data)
        counts[outcome] += 1
    counts["dst_only"] = len(dst_keys - set(src_keys))
    return counts
