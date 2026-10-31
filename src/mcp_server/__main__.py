"""Run the MacroRAG MCP server.

    python -m mcp_server                      # stdio (local subprocess integration)
    python -m mcp_server --transport sse      # expose over SSE
    python -m mcp_server --transport streamable-http

``--principal`` stamps a caller identity on every retrieval (the MCP analogue of
an API key's principal mapping). The corpus is a shared public knowledge base and
no ingest path writes ``tenant_id`` today, so this is a dormant hook, NOT data
isolation (see docs/design/Macro_RAG_Production_Hardening.md §A); omit for parity.
``--tenant`` is accepted as a deprecated alias.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys

from utils.logging import get_logger, setup_logging

from .server import build_mcp

log = get_logger(__name__)

# Modules the tool path imports lazily that load native extensions. On Windows, a
# first import of one of these from a worker thread while the stdio transport has
# a read pending on stdin deadlocks until stdin closes (seen from Faultline: every
# retrieval call hung on `import numpy`, pulled in by qdrant_client). Importing
# them on the main thread before the transport starts makes every later import a
# sys.modules lookup. Optional ones (e.g. anthropic) may be absent.
_PRELOAD_MODULES = (
    "numpy",
    "qdrant_client",
    "qdrant_client.models",
    "openai",
    "anthropic",
    "pipelines.query_pipeline",
)


def _preload_modules() -> None:
    for name in _PRELOAD_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            log.debug("preload skipped: %s not installed", name)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="macro-rag-mcp")
    p.add_argument("--transport", default="stdio",
                   choices=["stdio", "sse", "streamable-http"])
    # --tenant kept as a deprecated alias so existing MCP client configs keep working
    p.add_argument("--principal", "--tenant", dest="principal", default=None,
                   help="stamp this caller identity on retrieval (not data isolation)")
    p.add_argument("--no-warmup", action="store_true",
                   help="don't pre-build the pipeline at startup (lazy first call)")
    args = p.parse_args(argv)
    # HF tokenizers warns and can stall if the process later forks; we never need
    # its parallelism here, so silence it before any model import.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    setup_logging()
    # Before build_mcp: its warmup thread imports the pipeline too.
    _preload_modules()
    build_mcp(principal=args.principal,
              warmup=not args.no_warmup).run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
