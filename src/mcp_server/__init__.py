"""MacroRAG MCP server — in-process tool surface over the query pipeline."""

from __future__ import annotations

from .server import build_mcp

__all__ = ["build_mcp"]
