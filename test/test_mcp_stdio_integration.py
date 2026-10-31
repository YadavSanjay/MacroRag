"""End-to-end stdio integration: launch the MCP server as a real subprocess the
exact way Faultline's ScenarioGen does

    python -m mcp_server --transport stdio --tenant scenariogen

and drive it over the real JSON-RPC/stdio transport with the MCP client. This is
the regression guard for the reported "MCP deadlock": it proves the server
completes the ``initialize`` handshake, lists its tools, and round-trips tool
calls without the event loop freezing or stdout being corrupted by logging.

We pass ``--no-warmup`` and exercise the two pipeline-free tools (vocabulary +
shock schema) so the test needs no Qdrant server, models, or OpenAI key — it is
a pure transport/protocol test. The heavy-pipeline path is covered by the
in-process concurrency test in ``test_mcp.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

_EXPECTED_TOOLS = {
    "macro_query", "macro_synthesize_shock_vector", "macro_discover_factors",
    "macro_get_citations", "macro_vocabulary", "macro_shock_schema",
}


def _server_params() -> StdioServerParameters:
    # The child runs outside the conftest guard, so isolate it the same way: no
    # dotenv file, logs and cwd in the test's temp dir (set by test/conftest.py).
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server", "--transport", "stdio",
              "--tenant", "scenariogen", "--no-warmup"],
        cwd=os.getcwd(),
        env={"PYTHONPATH": str(SRC), "TOKENIZERS_PARALLELISM": "false",
             "MACRORAG_DOTENV": "0", "MACRORAG_LOG_DIR": os.environ["MACRORAG_LOG_DIR"]},
    )


async def _drive():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            t0 = time.perf_counter()
            await session.initialize()
            init_s = time.perf_counter() - t0

            tools = {t.name for t in (await session.list_tools()).tools}

            vocab = await session.call_tool("macro_vocabulary", {})
            schema = await session.call_tool("macro_shock_schema", {})
            return init_s, tools, vocab, schema


def test_stdio_server_handshakes_and_serves_tools():
    # generous ceiling: subprocess spawn + imports, but proves no hang/deadlock
    init_s, tools, vocab, schema = asyncio.run(asyncio.wait_for(_drive(), timeout=60))

    assert _EXPECTED_TOOLS <= tools, f"missing tools: {_EXPECTED_TOOLS - tools}"

    # tool results round-tripped cleanly over stdio (the JSON parsed back out of
    # the content block proves stdout was NOT corrupted by stray logging) ...
    assert not vocab.isError and not schema.isError
    vocab_data = json.loads(vocab.content[0].text)
    assert "factor_tags" in vocab_data
    schema_data = json.loads(schema.content[0].text)
    names = {f["name"] for f in schema_data.get("fields", [])}
    assert {"UST_10Y", "IG_OAS", "USDTWD"} <= names

    # handshake was prompt — warmup (when enabled) never blocks startup, and with
    # --no-warmup the heavy pipeline is never even built.
    assert init_s < 30, f"initialize took {init_s:.1f}s — startup is blocking"
