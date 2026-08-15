"""Guardrail: total tool-schema size must stay under budget.

Tool schemas load into every client session's context window. This fork keeps
deliberately rich, A-grade docstrings on the full surface (they measurably
improve tool selection) AND offers the search gateway / profiles for when
context is tight. So the guard is two-sided:

* the full surface may not silently balloon past FULL_BUDGET_BYTES, and
* the gateway must stay small — that is the whole point of it.

If the full test fails, trim docstrings or move prose to INSTRUCTIONS.md; do
NOT just raise the budget. If the gateway test fails, a core tool crept in.

Measured 2026-08-15: full 56 tools ~43.5 KB; gateway 10 tools ~10 KB.
"""

from __future__ import annotations

import asyncio
import json
import os

FULL_BUDGET_BYTES = 46_000
GATEWAY_BUDGET_BYTES = 12_000


def _schema_bytes() -> tuple[int, int]:
    # imported inside so env changes take effect per-process invocation
    from src.tools import mcp

    tools = asyncio.run(mcp.list_tools())
    blob = json.dumps(
        [
            {"name": t.name, "description": t.description or "", "schema": t.inputSchema}
            for t in tools
        ]
    )
    return len(tools), len(blob)


def test_full_surface_schema_under_budget() -> None:
    count, size = _schema_bytes()
    assert size <= FULL_BUDGET_BYTES, (
        f"full surface: {count} tools total {size} bytes "
        f"(budget {FULL_BUDGET_BYTES}); trim docstrings or consolidate, "
        "do not raise the budget"
    )


def test_gateway_surface_stays_small() -> None:
    # Enable the gateway for a subprocess so registration re-runs with the env set.
    import subprocess
    import sys

    code = (
        "import json,asyncio;"
        "from src.tools import mcp;"
        "tools=asyncio.run(mcp.list_tools());"
        "blob=json.dumps([{'n':t.name,'d':t.description or '','s':t.inputSchema} for t in tools]);"
        "print(len(tools), len(blob))"
    )
    env = dict(os.environ, ZENDRIVER_MCP_GATEWAY="1")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.check_output([sys.executable, "-c", code], env=env, cwd=root)
    count, size = (int(x) for x in out.split())
    assert count <= 12, f"gateway exposed {count} tools; expected ~10 (core + 3 meta)"
    assert size <= GATEWAY_BUDGET_BYTES, (
        f"gateway surface {size} bytes exceeds budget {GATEWAY_BUDGET_BYTES}"
    )
