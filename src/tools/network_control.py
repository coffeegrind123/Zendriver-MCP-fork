"""Network-layer controls: URL blocking, extra headers, service worker bypass.

Complements the existing ``LoggingTools`` (passive observation). These tools
*change* what the browser does on the wire.
"""

from __future__ import annotations

from zendriver import cdp

from src.errors import ZendriverMCPError
from src.tools.base import ToolBase


class NetworkControlTools(ToolBase):
    """Active network controls that modify outgoing requests."""

    def _register_tools(self) -> None:
        self._register(self.block_urls)
        self._register(self.unblock_all_urls)
        self._register(self.set_extra_headers)
        self._register(self.bypass_service_worker)

    async def block_urls(self, patterns: list[str]) -> str:
        """Block subsequent requests whose URL matches one of the patterns.

        Patterns use Chrome's wildcard syntax: ``*googletagmanager.com*``,
        ``*.png``, ``https://analytics.example.com/*``. Pass an empty list
        or call ``unblock_all_urls`` to remove all blocks.
        """
        if not isinstance(patterns, list):
            raise ZendriverMCPError("patterns must be a list of strings")
        await self.session.page.send(cdp.network.set_blocked_ur_ls(urls=patterns))
        return f"Blocking {len(patterns)} URL pattern(s)" if patterns else "URL block-list cleared"

    async def unblock_all_urls(self) -> str:
        """Remove every URL-pattern block."""
        await self.session.page.send(cdp.network.set_blocked_ur_ls(urls=[]))
        return "URL block-list cleared"

    async def set_extra_headers(self, headers: dict[str, str]) -> str:
        """Add headers to every outgoing request on the current tab.

        Useful for API keys, tenant overrides, tracing ids. Pass an empty
        dict to clear.
        """
        if not isinstance(headers, dict):
            raise ZendriverMCPError("headers must be a dict[str, str]")
        await self.session.page.send(
            cdp.network.set_extra_http_headers(headers=cdp.network.Headers(headers))
        )
        return f"Extra headers set: {list(headers.keys())}" if headers else "Extra headers cleared"

    async def bypass_service_worker(self, bypass: bool = True) -> str:
        """Skip the service worker and always go to the network."""
        await self.session.page.send(cdp.network.set_bypass_service_worker(bypass=bypass))
        return f"Service worker bypass: {bypass}"
