# base class for all tool modules
import asyncio
import functools
import inspect
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.errors import (
    BrowserNotStartedError,
    BrowserUnreachableError,
    ElementNotFoundError,
    ToolTimeoutError,
    ZendriverMCPError,
)
from src.session import BrowserSession, tool_timeout_budget

# How long a single JavaScript evaluation may take before it is called hung.
# Generous enough for a heavy DOM walk, short enough to stay inside the MCP
# client's own request timeout (30s in this stack's mcp/adapter.json) so the
# caller gets a real message instead of a transport timeout.
JS_TIMEOUT_SECONDS = 20.0


# One definition of the tool budget, shared with the launch path: a Chrome
# launch has to finish inside it or its diagnosis is cancelled before it can be
# reported. Two copies of this number drifting apart is exactly how a dead
# $DISPLAY reached a caller as a bare transport timeout.
_default_tool_timeout = tool_timeout_budget

DEFAULT_TOOL_TIMEOUT = _default_tool_timeout()


def _autostart_enabled() -> bool:
    return os.environ.get("ZENDRIVER_MCP_AUTOSTART_BROWSER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# Opt-in: open the browser on the first tool that needs one, instead of
# returning "Browser not started. Call start_browser first."
#
# Worth having because the alternative costs a client either a wasted turn or a
# tool schema. start_browser's schema is 2.3 KB (~570 tokens) — the largest on
# the server — so a client that only wants the common browse loop otherwise pays
# that just to satisfy a precondition it has no decision to make about.
#
# Off by default: a client that manages its own lifecycle (headless, a proxy, a
# persistent profile) must be able to call start_browser with its own arguments
# before anything else launches Chrome with defaults.
AUTOSTART_BROWSER = _autostart_enabled()

# One lock for the process: two concurrent tool calls on a cold session would
# otherwise both see "no browser" and race two Chrome launches.
_autostart_lock = asyncio.Lock()

# Must match ``ATTR`` in src/static/js/dom_walker.js.
ZENDRIVER_ID_ATTR = "data-zendriver-id"


def _timeout_error(name: str, budget: float, elapsed: float, exc: BaseException) -> BaseException:
    """Tell "the tool ran too long" apart from "something inside it timed out".

    They arrive as the same exception type and mean opposite things. zendriver
    signals a MISS as a timeout — `find(text=...)` raises
    `asyncio.TimeoutError("Timeout (10s) waiting for any element with text: ...")`
    when nothing matches — so reporting every asyncio.TimeoutError as the tool's
    own budget turns "no element matched" into "the tool was too slow".

    Observed: a click that returned in 10.4s reported "exceeded its 25s time
    budget". The caller read that as a slow browser and spent several turns
    retrying, guessing URLs and falling back to curl. The element simply was not
    there, which zendriver had said clearly and this had thrown away.

    Elapsed time is what separates them: the outer wait_for cannot fire early.
    """
    if elapsed < budget * 0.9:
        message = str(exc).strip() or "an internal operation timed out"
        return ZendriverMCPError(
            f"{message} (this is not the tool's {budget:.0f}s budget — it returned "
            f"after {elapsed:.1f}s, so the operation itself gave up)"
        )
    return ToolTimeoutError(name, budget)


class ToolBase(ABC):
    """base class providing shared functionality for all tool modules"""

    def __init__(self, mcp: FastMCP):
        self._mcp = mcp
        self._session = BrowserSession.get_instance()
        # Auto-register the tool's session-reset hook if it defines one.
        # Stateful tools (interception rules, trace buffers, screencast
        # handles, accessibility uid caches) implement ``_reset_state``;
        # this saves every such subclass from repeating the registration.
        reset = getattr(self, "_reset_state", None)
        if callable(reset):
            self._session.register_reset_callback(reset)
        self._register_tools()

    @staticmethod
    def resolve_selector(selector: str) -> str:
        """Turn a numeric id from ``get_interaction_tree`` into a CSS selector.

        The DOM walker tags interactive elements with ``data-zendriver-id``;
        tools accept either a real CSS selector or that numeric id. This helper
        centralises the conversion so every tool treats them the same way.
        """
        if selector.isdigit():
            return f'[{ZENDRIVER_ID_ATTR}="{selector}"]'
        return selector

    async def try_select(self, selector: str, timeout: float = 2.0):
        """Select an element, returning ``None`` on miss instead of raising.

        Use when the logic legitimately branches on presence (e.g. a
        visibility probe before clicking).
        """
        import asyncio as _asyncio

        try:
            return await self._session.page.select(selector, timeout=timeout)
        except _asyncio.TimeoutError:
            return None

    @abstractmethod
    def _register_tools(self) -> None:
        """override in subclasses to register tools with mcp"""

    def _register(
        self,
        fn: "Callable[..., Coroutine[Any, Any, Any]]",
        timeout: float | None = None,
    ) -> None:
        """Register ``fn`` as an MCP tool, guarded by a time budget so one hung CDP call
        can never freeze the whole MCP session. Budget defaults to DEFAULT_TOOL_TIMEOUT
        (120s, or $ZENDRIVER_MCP_TOOL_TIMEOUT); override per slow tool. (Ported from bituq.)"""
        budget = float(timeout) if timeout is not None else DEFAULT_TOOL_TIMEOUT
        name = fn.__name__

        @functools.wraps(fn)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=budget)
            except asyncio.TimeoutError as exc:
                err = _timeout_error(name, budget, time.monotonic() - started, exc)
                # A wedged Chrome shows up HERE and nowhere else. The recovery
                # below only runs from `except Exception`, so a browser that hangs
                # instead of exiting never reaches it: is_dead() is
                # `browser.stopped`, and a hung process has not stopped. Measured
                # 2026-09-02 -- a crashpad ptrace deadlock left the DevTools port
                # accepting connections and answering nothing for 22 hours while
                # every call returned this same timeout and nothing self-healed.
                # Probing the endpoint is what tells "slow" from "gone".
                if isinstance(err, ToolTimeoutError):
                    if await self._session.discard_if_unreachable():
                        raise BrowserUnreachableError(
                            name, budget, AUTOSTART_BROWSER
                        ) from exc
                raise err from exc
            except BrowserNotStartedError:
                # Retried once, and only once: if the browser still is not there
                # after a start, something is wrong with the launch and the
                # caller needs to see that error rather than a loop.
                if not AUTOSTART_BROWSER or name == "start_browser":
                    raise
                async with _autostart_lock:
                    if self._session.is_running():
                        pass  # another call won the race and started it
                    else:
                        await self._session.start()
                retried = time.monotonic()
                try:
                    return await asyncio.wait_for(fn(*args, **kwargs), timeout=budget)
                except asyncio.TimeoutError as exc:
                    raise _timeout_error(name, budget, time.monotonic() - retried, exc) from exc
            except Exception:
                # Chrome died underneath a live session — crash, OOM kill, a user
                # closing the window. The session still holds a Browser object,
                # so this arrives as a websocket error rather than
                # BrowserNotStartedError, and every later call fails the same way
                # until someone restarts it by hand.
                #
                # is_dead() is the whole guard: an ordinary tool failure with a
                # healthy browser is re-raised untouched.
                if not AUTOSTART_BROWSER or name == "start_browser":
                    raise
                if not self._session.is_dead():
                    raise
                async with _autostart_lock:
                    if self._session.is_dead():
                        self._session.discard()
                    if not self._session.is_running():
                        await self._session.start()
                retried = time.monotonic()
                try:
                    return await asyncio.wait_for(fn(*args, **kwargs), timeout=budget)
                except asyncio.TimeoutError as exc:
                    raise _timeout_error(name, budget, time.monotonic() - retried, exc) from exc

        # Preserve the signature so FastMCP introspects the right schema, and drop
        # __wrapped__ so it doesn't follow back to the unbound function (exposing `self`).
        guarded.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
        if hasattr(guarded, "__wrapped__"):
            delattr(guarded, "__wrapped__")
        guarded.__zendriver_timeout__ = budget  # type: ignore[attr-defined]
        self._mcp.tool()(guarded)

    @property
    def session(self) -> BrowserSession:
        """get browser session instance"""
        return self._session

    @property
    def mcp(self) -> FastMCP:
        """get mcp server instance"""
        return self._mcp

    @staticmethod
    def escape_js_string(s: str) -> str:
        """escape special characters for safe JavaScript string interpolation"""
        return (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )

    async def get_element(self, selector: str):
        """get element by selector, raise error if not found"""
        elem = await self._session.page.select(selector)
        if elem is None:
            raise ElementNotFoundError(selector)
        return elem

    async def get_element_by_text(self, text: str):
        """get element by text content, raise error if not found"""
        elem = await self._session.page.find(text, best_match=True)
        if elem is None:
            raise ElementNotFoundError(f"text='{text}'")
        return elem

    async def run_js(self, script: str, timeout: float = JS_TIMEOUT_SECONDS) -> Any:
        """Execute JavaScript and return the result, refusing to hang forever.

        `page.evaluate()` can stop returning altogether. Observed on a real tab
        that had been left mid-navigation: `get_page_info` answered in 0.4s (it
        reads cached attributes and makes no CDP round trip) while
        `get_text_content` never came back at all, because every JS evaluation
        against that tab hung. From the caller's side that is not an error, it is
        silence — the MCP request times out and returns a parameter dump, which
        says nothing about the browser and reads as a bad argument.

        A bound turns that into a sentence naming the real problem. It has to
        live here rather than in each tool: every JS-based tool shares the
        failure, and the ones that need longer can ask for it.
        """
        try:
            return await asyncio.wait_for(self._session.page.evaluate(script), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise ToolTimeoutError("run_js", timeout) from exc

    async def check_visibility(self, selector: str) -> dict:
        """check if element exists and is visible"""
        safe_sel = self.escape_js_string(selector)
        return await self.run_js(f'''
            (function() {{
                const el = document.querySelector("{safe_sel}");
                if (!el) return {{ found: false }};
                const style = window.getComputedStyle(el);
                const hidden = style.display === "none" || style.visibility === "hidden";
                return {{ found: true, hidden: hidden, tag: el.tagName }};
            }})()
        ''')

    async def wait_for_condition(
        self, check_fn: Callable, timeout: float, poll_interval: float = 0.5
    ) -> bool:
        """wait for a condition to be true within timeout"""
        start = time.time()
        while time.time() - start < timeout:
            if await check_fn():
                return True
            await self._session.page.wait(poll_interval)
        return False

    @staticmethod
    def truncate(text: str, max_length: int, suffix: str = "\n... (truncated)") -> str:
        """truncate text if it exceeds max length"""
        if len(text) > max_length:
            return text[:max_length] + suffix
        return text

    @staticmethod
    def bool_to_yes_no(value: bool) -> str:
        """convert boolean to Yes/No string"""
        return "Yes" if value else "No"
