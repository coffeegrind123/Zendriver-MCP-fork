"""Opt-in auto-start: a tool that needs a browser opens one instead of failing.

``AUTOSTART_BROWSER`` is read at import time, so each mode runs in a subprocess.
No real Chrome is launched — ``BrowserSession.start`` is monkeypatched, which is
also what makes the "started exactly once" assertions meaningful.
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Registers one tool through ToolBase._register, then calls it on a cold session.
HARNESS = """
import asyncio, json
from src.errors import BrowserNotStartedError
from src.session import BrowserSession
from src.tools.base import ToolBase
from mcp.server.fastmcp import FastMCP

starts = []
session = BrowserSession.get_instance()

async def fake_start(*a, **k):
    starts.append(1)
    session._browser = object()      # what a real start leaves behind
    return session._browser

session.start = fake_start           # instance attribute wins over the class

class Probe(ToolBase):
    def _register_tools(self):
        self._register(self.needs_browser)

    async def needs_browser(self) -> str:
        _ = self._session.browser     # raises BrowserNotStartedError when cold
        return "ok"

mcp = FastMCP("test")
probe = Probe(mcp)

async def main():
    try:
        out = await mcp._tool_manager._tools["needs_browser"].fn()
    except BrowserNotStartedError as e:
        out = f"RAISED:{e}"
    print(json.dumps({"out": out, "starts": len(starts)}))

asyncio.run(main())
"""


def _run(**env_extra: str) -> dict:
    import json

    env = dict(os.environ, **env_extra)
    env.pop("ZENDRIVER_MCP_AUTOSTART_BROWSER", None)
    env.update(env_extra)
    raw = subprocess.check_output([sys.executable, "-c", HARNESS], env=env, cwd=ROOT).decode()
    return json.loads(raw.strip().splitlines()[-1])


def test_off_by_default_the_error_still_reaches_the_caller() -> None:
    result = _run()
    assert result["out"].startswith("RAISED:Browser not started"), result
    assert result["starts"] == 0


def test_enabled_starts_the_browser_once_and_retries() -> None:
    result = _run(ZENDRIVER_MCP_AUTOSTART_BROWSER="1")
    assert result["out"] == "ok", result
    assert result["starts"] == 1, result


def test_start_browser_itself_is_never_auto_started() -> None:
    """Otherwise a failing launch would be retried inside its own error path."""
    harness = HARNESS.replace("needs_browser", "start_browser")
    env = dict(os.environ, ZENDRIVER_MCP_AUTOSTART_BROWSER="1")
    raw = subprocess.check_output([sys.executable, "-c", harness], env=env, cwd=ROOT).decode()
    import json

    result = json.loads(raw.strip().splitlines()[-1])
    assert result["out"].startswith("RAISED:Browser not started"), result
    assert result["starts"] == 0, result
