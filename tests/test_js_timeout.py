"""A hung `page.evaluate()` must be a sentence, not silence.

Observed 2026-08-17 on a tab left mid-navigation: `get_page_info` answered in
0.4s — it reads cached attributes and makes no CDP round trip — while
`get_text_content` never returned at all, because every JS evaluation against
that tab hung. The caller saw only its own transport timeout, followed by a dump
of the tool's parameters, which names nothing about the browser and reads as a
bad argument.
"""

from __future__ import annotations

import asyncio

import pytest

from src.errors import ToolTimeoutError
from src.tools.base import JS_TIMEOUT_SECONDS, ToolBase


class _HungPage:
    async def evaluate(self, _script: str):
        await asyncio.Event().wait()  # never returns, like the real thing


class _FastPage:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    async def evaluate(self, script: str):
        self.scripts.append(script)
        return "ok"


class _Session:
    def __init__(self, page) -> None:
        self.page = page


class _Tools(ToolBase):
    """ToolBase is abstract; the smallest concrete thing that has run_js."""

    def _register_tools(self) -> None:  # pragma: no cover - never called here
        pass


def _tools(page) -> ToolBase:
    tools = _Tools.__new__(_Tools)
    tools._session = _Session(page)
    return tools


def test_a_hung_evaluation_raises_a_named_timeout() -> None:
    with pytest.raises(ToolTimeoutError) as caught:
        asyncio.run(_tools(_HungPage()).run_js("document.title", timeout=0.2))
    message = str(caught.value)
    assert "run_js" in message
    assert "time budget" in message


def test_the_budget_is_below_the_clients_request_timeout() -> None:
    # pi's adapter gives up at 30s (mcp/adapter.json requestTimeoutMs). A JS
    # bound at or above that would never be the thing that answers first, which
    # is the entire point of having it.
    assert JS_TIMEOUT_SECONDS < 30.0


def test_an_ordinary_evaluation_is_untouched() -> None:
    page = _FastPage()
    assert asyncio.run(_tools(page).run_js("document.title")) == "ok"
    assert page.scripts == ["document.title"]
