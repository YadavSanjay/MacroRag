"""Shared utilities for the Macro RAG layer: config, schema, corpus loading."""

from __future__ import annotations

from .config import RagSettings, load_settings
from .logging import current_query_id, get_logger, reset_query_id, set_query_id, setup_logging
from .schema import (
                      Answer,
                      AnswerDetail,
                      ChunkPoint,
                      Embedding,
                      MetaFilter,
                      QueryAnalysis,
                      RetrievedChunk,
                      SparseVector,
                      TimeseriesHit,
)
from .text import estimate_tokens, grounded_text, grounding_prefix, point_uuid

__all__ = [
    "RagSettings", "load_settings",
    "get_logger", "setup_logging",
    "set_query_id", "reset_query_id", "current_query_id",
    "Answer", "AnswerDetail", "ChunkPoint", "Embedding", "MetaFilter",
    "QueryAnalysis", "RetrievedChunk", "SparseVector", "TimeseriesHit",
    "estimate_tokens", "grounded_text", "grounding_prefix", "point_uuid",
]
