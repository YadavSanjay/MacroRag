"""The retrieval orchestrator (ARag's HyDE-retriever, adapted to MacroRAG).

Flow: analyze query -> facet filters (+ optional LLM paraphrase/decompose/HyDE)
-> per-branch hybrid search (filtered) -> cross-branch RRF -> rerank against the
original question -> optional MMR diversify -> neighbour (prev/next) expansion ->
final_k chunks. Providers are injected, so the whole thing runs under test with
the hashing embedder + in-memory store + identity reranker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace as dc_replace
from typing import Optional, Protocol

from embeddings.base import Embedder
from utils.config import RankingSettings, RetrievalSettings
from utils.logging import get_logger
from utils.schema import MetaFilter, QueryAnalysis, RetrievedChunk
from vectorstores.base import VectorStore
from vectorstores.filters import match_payload

from .analysis import HeuristicAnalyzer
from .fusion import reciprocal_rank_fusion
from .mmr import mmr_select
from .ranking import apply_time_decay
from .rerank import IdentityReranker, Reranker

log = get_logger(__name__)

# Result size of the scenario-study search behind reserved slots. On the
# 2026-10 corpus ~130 chunks of three Taiwan papers share the study tags, so a
# 40-hit search never reached the EBA adverse-scenario document.
_STUDY_SEARCH_LIMIT = 400


class Analyzer(Protocol):
    def analyze(self, query: str, *, base_filter: MetaFilter | None = None
                ) -> QueryAnalysis: ...


class Retriever:
    def __init__(self, embedder: Embedder, store: VectorStore,
                 reranker: Reranker | None = None,
                 analyzer: Analyzer | None = None,
                 settings: RetrievalSettings | None = None,
                 ranking: RankingSettings | None = None):
        self.embedder = embedder
        self.store = store
        self.reranker = reranker or IdentityReranker()
        self.analyzer = analyzer or HeuristicAnalyzer()
        self.s = settings or RetrievalSettings()
        self.ranking = ranking or RankingSettings()

    def retrieve(self, query: str, *, base_filter: MetaFilter | None = None,
                 rerank_candidates: int | None = None,
                 reserve_tags: Sequence[str] = (), reserve_slots: int = 0,
                 ) -> tuple[list[RetrievedChunk], QueryAnalysis]:
        """Retrieve the final chunks for ``query``.

        Args:
            query: The user question.
            base_filter: Caller facet filter, merged with the extracted one.
            rerank_candidates: Rerank pool cap for this call; None = settings.
                CPU rerank cost is per document, so this is the latency lever.
            reserve_tags: Factor tags marking chunks eligible for a reserved slot.
            reserve_slots: Up to this many winners go to filter-passing chunks
                carrying ``reserve_tags`` (found by one extra tag-restricted
                search), replacing the lowest-ranked others; the winner count is
                unchanged and the reserved chunks count inside the rerank cap.

        Returns:
            (winners, analysis).
        """
        cap = rerank_candidates if rerank_candidates is not None else self.s.rerank_candidates
        analysis = self.analyzer.analyze(query, base_filter=base_filter)
        branches, embeddings = self._search_branches(analysis)
        fused = reciprocal_rank_fusion(branches)
        slots = reserve_slots if reserve_tags else 0
        reservable: list[RetrievedChunk] = []
        if slots > 0 and embeddings:
            reservable = self._reservable(fused, embeddings[0], analysis.filters,
                                          set(reserve_tags))
        pool, extra = _rerank_pool(fused, reservable, cap,
                                   protect=min(self.s.final_k, cap), slots=slots)
        if not pool:
            return [], analysis

        reranked = self.reranker.rerank(query, pool, top_n=cap)
        reranked = apply_time_decay(reranked, self.ranking)   # latest-supersedes boost
        if slots > 0:
            winners = self._reserve(query, reranked, {c.chunk_id for c in extra},
                                    {c.chunk_id for c in reservable}, slots,
                                    n=min(self.s.final_k, len(pool)))
        else:
            winners = self._mmr(query, reranked)[: self.s.final_k]
        if self.s.chunk_expansion:
            winners = self._expand_neighbours(winners)
        return winners, analysis

    # ── internals ────────────────────────────────────────────────────────────

    def _reservable(self, fused, query_vec, filt: MetaFilter,
                    tags: set[str]) -> list[RetrievedChunk]:
        """Chunks eligible for a reserved slot: carrying ``tags`` and passing the
        full ``filt``. Fused ones first (in fused order), then the hits of one
        tag-restricted search, which finds studies the branches ranked out."""
        def eligible(c: RetrievedChunk) -> bool:
            return bool(tags & set(c.payload.get("factor_tags") or [])) and \
                match_payload(filt, c.payload)

        tag_filt = dc_replace(filt, factor_tags=sorted(tags))
        # Wide on purpose: a few long papers sharing the tags can fill a
        # fusion_limit-sized result and hide whole documents; vector search is cheap.
        extra = self.store.search(query_vec, limit=_STUDY_SEARCH_LIMIT, filt=tag_filt)
        out: list[RetrievedChunk] = []
        seen: set[str] = set()
        for c in [*fused, *extra]:
            if c.chunk_id not in seen and eligible(c):
                seen.add(c.chunk_id)
                out.append(c)
        return out

    def _reserve(self, query: str, reranked: list[RetrievedChunk], extra_ids: set[str],
                 reservable_ids: set[str], slots: int, *, n: int) -> list[RetrievedChunk]:
        """The ``n`` winners with up to ``slots`` places given to new documents.

        The base winners W0 are the top of the pool without the reserved extras.
        A new document is one with a reservable chunk in the pool but no chunk in
        W0; its best chunk replaces the lowest-ranked W0 chunk, one per document.
        A document already in W0 uses no slot, so when there is none the
        winners are exactly W0.
        """
        base = [c for c in reranked if c.chunk_id not in extra_ids]
        w0 = self._mmr(query, base)[: self.s.final_k]
        w0_docs = {_doc_of(c) for c in w0}
        new = _one_per_doc([c for c in reranked if c.chunk_id in reservable_ids
                            and _doc_of(c) not in w0_docs])
        take = new[: min(slots, n)]
        return w0[: n - len(take)] + take

    def _search_branches(self, analysis: QueryAnalysis
                         ) -> tuple[list[list[RetrievedChunk]], list]:
        queries = analysis.search_queries()
        embeddings = self.embedder.embed_queries(queries)   # one batched model call
        branches = self._parallel_search(embeddings, analysis.filters)
        # If a strict filter starved retrieval, retry once unfiltered (recall guard).
        if not any(branches) and not analysis.filters.is_empty():
            log.info("filtered retrieval empty; retrying without facet filter")
            branches = [self.store.search(embeddings[0], limit=self.s.fusion_limit,
                                          filt=None)]
        return branches, embeddings

    def _parallel_search(self, embeddings, filt) -> list[list[RetrievedChunk]]:
        """Fan retrieval branches out across a thread pool. decompose+HyDE can
        spawn several independent searches; running them concurrently cuts query
        latency. Order is preserved for deterministic downstream fusion."""
        def _one(emb):
            return self.store.search(emb, limit=self.s.fusion_limit, filt=filt)

        if len(embeddings) <= 1 or self.s.max_branch_workers <= 1:
            return [_one(e) for e in embeddings]
        from concurrent.futures import ThreadPoolExecutor
        workers = min(self.s.max_branch_workers, len(embeddings))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(_one, embeddings))

    def _mmr(self, query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if self.s.mmr_lambda >= 1.0 or len(chunks) <= self.s.final_k:
            return chunks
        qvec = self.embedder.embed_query(query).dense
        cand_vecs = self.embedder.embed_documents([c.text for c in chunks])
        pairs = [(c.chunk_id, v.dense) for c, v in zip(chunks, cand_vecs)]
        order = mmr_select(qvec, pairs, k=self.s.final_k, lambda_=self.s.mmr_lambda)
        by_id = {c.chunk_id: c for c in chunks}
        return [by_id[cid] for cid in order]

    def _expand_neighbours(self, winners: list[RetrievedChunk]) -> list[RetrievedChunk]:
        have = {c.chunk_id for c in winners}
        neighbour_ids = []
        for c in winners:
            for key in ("prev_chunk_id", "next_chunk_id"):
                nid = c.payload.get(key)
                if nid and nid not in have:
                    neighbour_ids.append(nid)
                    have.add(nid)
        if not neighbour_ids:
            return winners
        neighbours = self.store.get_by_chunk_ids(neighbour_ids)
        for n in neighbours:
            n.payload["expanded"] = True
        return winners + neighbours


def _doc_of(c: RetrievedChunk) -> str:
    """A chunk's document id (payload ``doc_id``, else the chunk-id prefix)."""
    return str(c.payload.get("doc_id") or c.chunk_id.split("::", 1)[0])


def _one_per_doc(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """The first (best-ranked) chunk of each document, order kept."""
    seen: set[str] = set()
    out = []
    for c in chunks:
        d = _doc_of(c)
        if d not in seen:
            seen.add(d)
            out.append(c)
    return out


def _rerank_pool(fused: list[RetrievedChunk], reservable: list[RetrievedChunk],
                 cap: int, *, protect: int, slots: int
                 ) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
    """The rerank pool (``cap`` chunks at most) and its reserve candidates.

    Order: the likely base winners ``fused[:protect]``, then one chunk for each
    of up to ``slots`` new documents (reservable, no chunk in those winners), then
    the remaining candidates. When the cap is tight the protected part gives way
    from the bottom, as the reserved chunks will replace those winners anyway.
    """
    protected = fused[:protect]
    taken_docs = {_doc_of(c) for c in protected}
    candidates = _one_per_doc([c for c in reservable if _doc_of(c) not in taken_docs])
    extra = candidates[: min(slots, cap)]
    head = protected[: cap - len(extra)]
    chosen = {c.chunk_id for c in [*head, *extra]}
    rest = [c for c in [*fused[protect:], *reservable] if c.chunk_id not in chosen]
    fill: list[RetrievedChunk] = []
    for c in rest:
        if len(head) + len(extra) + len(fill) >= cap:
            break
        if c.chunk_id not in chosen:
            chosen.add(c.chunk_id)
            fill.append(c)
    return [*head, *extra, *fill], extra
