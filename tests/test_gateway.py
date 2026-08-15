"""The search gateway must collapse the surface and still reach every tool.

Registration reads env at import time, so gateway mode runs in a subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(code: str, **env_extra: str) -> str:
    env = dict(os.environ, **env_extra)
    return subprocess.check_output([sys.executable, "-c", code], env=env, cwd=ROOT).decode()


def test_gateway_exposes_only_core_and_meta() -> None:
    code = (
        "from src.tools import mcp;import json;print(json.dumps(sorted(mcp._tool_manager._tools)))"
    )
    names = json.loads(_run(code, ZENDRIVER_MCP_GATEWAY="1"))
    assert {"search_tools", "describe_tool", "call_tool"} <= set(names)
    assert "get_cookies" not in names  # hidden behind the gateway
    assert len(names) <= 12


def test_search_finds_hidden_tool_and_call_dispatches() -> None:
    code = """
import asyncio
from src.tools import mcp
tm = mcp._tool_manager
search = tm._tools['search_tools'].fn
call = tm._tools['call_tool'].fn


async def m():
    s = await search('read cookies', 5)
    assert 'get_cookies' in s, s
    err = await call('does_not_exist', {})
    assert 'Unknown tool' in err, err
    bad = await call('get_element_text', {})  # missing required selector
    assert 'Error' in bad and 'selector' in bad, bad
    print('OK')


asyncio.run(m())
"""
    assert "OK" in _run(code, ZENDRIVER_MCP_GATEWAY="1")


def test_profile_and_deny_filter() -> None:
    code = (
        "from src.tools import mcp;import json;print(json.dumps(sorted(mcp._tool_manager._tools)))"
    )
    names = set(
        json.loads(_run(code, ZENDRIVER_MCP_PROFILE="browse", ZENDRIVER_MCP_DENY="go_forward"))
    )
    assert "navigate" in names and "get_content" in names
    assert "go_forward" not in names  # denied
    assert "set_cookie" not in names  # storage group not in 'browse'
    assert "start_browser" in names  # lifecycle always retained


def test_default_surface_unchanged() -> None:
    code = "from src.tools import mcp; print(len(mcp._tool_manager._tools))"
    # explicitly clear any gateway/profile env for the child
    env_clear = {
        k: ""
        for k in (
            "ZENDRIVER_MCP_GATEWAY",
            "ZENDRIVER_MCP_PROFILE",
            "ZENDRIVER_MCP_GROUPS",
            "ZENDRIVER_MCP_ALLOW",
            "ZENDRIVER_MCP_DENY",
        )
    }
    assert _run(code, **env_clear).strip() == "98"
