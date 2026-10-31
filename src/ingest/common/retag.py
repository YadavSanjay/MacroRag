"""Re-apply configured tags to already-processed documents (spec retag-endpoint).

Chunk tags are stamped at ingest from the endpoint config, so a config change
reaches only future ingests. This rewrites the stored documents' chunk tags in
place, through the processed-document object store (local or S3), and changes
nothing else.
"""

from __future__ import annotations

from utils.objectstore import ObjectStore

from .models import ParsedDocument


def retag_documents(store: ObjectStore, *, tier: int, source_id: str,
                    factor_tags: list[str], entity_tags: list[str],
                    dry_run: bool = False) -> dict[str, int]:
    """Set every chunk's tags in one source's stored documents.

    Args:
        store: The processed-document object store.
        tier: The documents' tier number (the key prefix ``tier{tier}/``).
        source_id: The endpoint id (the key prefix's second part).
        factor_tags: The factor tags every chunk gets, in this order.
        entity_tags: The entity tags every chunk gets, in this order.
        dry_run: Count only; write nothing.

    Returns:
        Counts: ``docs`` read, ``changed_docs`` and ``changed_chunks``.
    """
    counts = {"docs": 0, "changed_docs": 0, "changed_chunks": 0}
    for key in store.iter_keys(f"tier{tier}/{source_id}/", suffix=".json"):
        text = store.get_text(key)
        if not text:
            continue
        counts["docs"] += 1
        doc = ParsedDocument.model_validate_json(text)
        changed = 0
        for chunk in doc.chunks:
            m = chunk.metadata
            if m.factor_tags == factor_tags and m.entity_tags == entity_tags:
                continue
            m.factor_tags = list(factor_tags)
            m.entity_tags = list(entity_tags)
            changed += 1
        if not changed:
            continue
        counts["changed_docs"] += 1
        counts["changed_chunks"] += changed
        if not dry_run:
            store.put_text(key, doc.model_dump_json(indent=2, exclude_none=True))
    return counts
