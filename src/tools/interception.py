"""Request interception: mock responses, fail requests.

Uses ``Fetch.enable`` + a single persistent ``requestPaused`` handler that
walks a list of rules and acts on the first match. Adding the first rule
enables interception; removing the last one disables it.

Unlike ``NetworkControlTools.block_urls`` (network-layer block, fires a
TCP error), this operates at the Fetch layer and can mock responses with
arbitrary status codes, headers, and bodies - handy for testing retry
logic, rate-limit handling, or offline paths.
"""

from __future__ import annotations

import asyncio
import base64
import fnmatch
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import FastMCP
from zendriver import cdp

from src.errors import ZendriverMCPError
from src.response import ToolResponse
from src.tools.base import ToolBase

PausedHandler = Callable[[cdp.fetch.RequestPaused], Awaitable[None]]

# Mock bodies are held entirely in memory before base64-encoding and being
# sent over CDP. 10 MiB is comfortably larger than anything a typical API
# would return; larger responses suggest accidental misuse or an abuse
# attempt rather than a legitimate mock.
_MAX_MOCK_BODY_BYTES = 10 * 1024 * 1024


@dataclass(slots=True)
class _Rule:
    id: str
    url_pattern: str  # fnmatch-style, e.g. "*/api/v1/*"
    action: str  # "mock" or "fail"
    status: int = 200
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error_reason: str = "Failed"  # matches cdp.network.ErrorReason members
    match_count: int = 0


class InterceptionTools(ToolBase):
    """Mock or fail outgoing requests matching URL patterns."""

    def __init__(self, mcp: FastMCP) -> None:
        self._rules: list[_Rule] = []
        self._handler: PausedHandler | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        # ToolBase.__init__ auto-registers the reset callback below.
        super().__init__(mcp)

    def _reset_state(self) -> None:
        self._rules = []
        self._handler = None
        self._next_id = 0

    def _register_tools(self) -> None:
        self._register(self.mock_response)
        self._register(self.fail_requests)
        self._register(self.list_interceptions)
        self._register(self.stop_interception)

    async def mock_response(
        self,
        url_pattern: str,
        status: int = 200,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return a mocked response for every request matching ``url_pattern``.

        ``url_pattern`` uses Unix glob syntax (``*``, ``?``), matched against
        the full request URL. ``body`` is UTF-8 text (cap: 10 MiB to prevent
        RAM exhaustion). Returns a rule id; pass it to
        ``stop_interception(rule_id)`` or call with no args to clear all.
        """
        if len(body.encode("utf-8")) > _MAX_MOCK_BODY_BYTES:
            raise ZendriverMCPError(
                f"mock body exceeds {_MAX_MOCK_BODY_BYTES // (1024 * 1024)} MiB cap"
            )
        rule = _Rule(
            id=self._fresh_id(),
            url_pattern=url_pattern,
            action="mock",
            status=status,
            body=body,
            headers=headers or {},
        )
        await self._add_rule(rule)
        return ToolResponse(
            summary=f"Mocking {url_pattern} -> HTTP {status}",
            data={"rule_id": rule.id, "active_rules": len(self._rules)},
        ).to_dict()

    async def fail_requests(
        self,
        url_pattern: str,
        error_reason: str = "Failed",
    ) -> dict[str, Any]:
        """Fail every request matching ``url_pattern`` with ``error_reason``.

        ``error_reason`` must match a CDP ``Network.ErrorReason`` member
        (``Failed``, ``Aborted``, ``TimedOut``, ``AccessDenied``,
        ``ConnectionClosed``, ``ConnectionReset``, ``ConnectionRefused``,
        ``ConnectionAborted``, ``ConnectionFailed``, ``NameNotResolved``,
        ``InternetDisconnected``, ``AddressUnreachable``, ``BlockedByClient``,
        ``BlockedByResponse``).
        """
        try:
            cdp.network.ErrorReason.from_json(error_reason)
        except ValueError as exc:
            raise ZendriverMCPError(f"Unknown error_reason: {error_reason}") from exc

        rule = _Rule(
            id=self._fresh_id(),
            url_pattern=url_pattern,
            action="fail",
            error_reason=error_reason,
        )
        await self._add_rule(rule)
        return ToolResponse(
            summary=f"Failing {url_pattern} with {error_reason}",
            data={"rule_id": rule.id, "active_rules": len(self._rules)},
        ).to_dict()

    async def list_interceptions(self) -> list[dict[str, Any]]:
        """Return the active interception rules with their match counts."""
        return [
            {
                "id": r.id,
                "url_pattern": r.url_pattern,
                "action": r.action,
                "status": r.status if r.action == "mock" else None,
                "error_reason": r.error_reason if r.action == "fail" else None,
                "match_count": r.match_count,
            }
            for r in self._rules
        ]

    async def stop_interception(self, rule_id: str | None = None) -> str:
        """Remove one rule by id, or all rules when ``rule_id`` is omitted."""
        async with self._lock:
            if rule_id is None:
                count = len(self._rules)
                self._rules.clear()
                await self._disable()
                return f"Cleared {count} interception rule(s)"

            for i, r in enumerate(self._rules):
                if r.id == rule_id:
                    self._rules.pop(i)
                    if not self._rules:
                        await self._disable()
                    return f"Removed rule {rule_id}"
        return f"No rule with id {rule_id}"

    def _fresh_id(self) -> str:
        self._next_id += 1
        return f"rule_{self._next_id:03d}"

    async def _add_rule(self, rule: _Rule) -> None:
        async with self._lock:
            self._rules.append(rule)
            if self._handler is None:
                await self._enable()

    async def _enable(self) -> None:
        tab = self.session.page
        handler = self._make_handler()
        self._handler = handler
        tab.add_handler(cdp.fetch.RequestPaused, handler)
        # Broad pattern: we filter in our handler so agents can add/remove
        # rules without re-configuring Fetch every time.
        await tab.send(
            cdp.fetch.enable(
                patterns=[cdp.fetch.RequestPattern(url_pattern="*")],
            )
        )

    async def _disable(self) -> None:
        if self._handler is None:
            return
        if not self.session.has_page():
            self._handler = None
            return
        tab = self.session.page
        handlers = tab.handlers.get(cdp.fetch.RequestPaused)
        if handlers and self._handler in handlers:
            handlers.remove(self._handler)
        try:
            await tab.send(cdp.fetch.disable())
        except Exception:
            # Tab may have closed; Fetch state goes with it.
            pass
        self._handler = None

    def _make_handler(self) -> PausedHandler:
        async def on_paused(event: cdp.fetch.RequestPaused) -> None:
            url = event.request.url
            # Snapshot rules AND capture the tab inside the lock - the
            # tab reference was previously read outside, so a concurrent
            # stop_browser could raise PageNotLoadedError here and leave
            # the paused request stuck forever in Chrome.
            async with self._lock:
                if not self.session.has_page():
                    return  # browser torn down; let the paused request die
                tab = self.session.page
                match = next(
                    (r for r in self._rules if fnmatch.fnmatch(url, r.url_pattern)),
                    None,
                )
                if match is not None:
                    match.match_count += 1

            if match is None:
                await tab.send(cdp.fetch.continue_request(request_id=event.request_id))
                return

            if match.action == "fail":
                await tab.send(
                    cdp.fetch.fail_request(
                        request_id=event.request_id,
                        error_reason=cdp.network.ErrorReason(match.error_reason),
                    )
                )
                return

            encoded_body = base64.b64encode(match.body.encode("utf-8")).decode("ascii")
            response_headers = [
                cdp.fetch.HeaderEntry(name=name, value=value)
                for name, value in match.headers.items()
            ]
            await tab.send(
                cdp.fetch.fulfill_request(
                    request_id=event.request_id,
                    response_code=match.status,
                    response_headers=response_headers or None,
                    body=encoded_body,
                )
            )

        return on_paused
