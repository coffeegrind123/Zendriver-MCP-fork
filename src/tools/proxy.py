"""Proxy configuration.

Chrome's proxy is set at launch via ``--proxy-server`` and cannot be changed
at runtime - even the DevTools Protocol has no primitive for it. So to switch
proxies we restart the browser. State that's tied to a user data directory
(cookies, localStorage, extensions) survives the restart; transient session
state doesn't.
"""

from __future__ import annotations

from src.errors import ZendriverMCPError
from src.tools.base import ToolBase


class ProxyTools(ToolBase):
    """Configure an HTTP/SOCKS proxy by restarting the browser."""

    def _register_tools(self) -> None:
        # Restart flow (stop + fresh launch) can easily exceed the default.
        self._register(self.configure_proxy, timeout=120)
        self._register(self.clear_proxy, timeout=120)

    async def configure_proxy(
        self,
        proxy_url: str,
        user_data_dir: str | None = None,
        headless: bool = False,
    ) -> str:
        """Restart the browser so it routes traffic through ``proxy_url``.

        Accepts ``http://host:port``, ``socks5://host:port``, or Chrome's
        full proxy rule syntax. Pass a ``user_data_dir`` to preserve the
        logged-in session across the restart - without it you start fresh.
        """
        if not proxy_url:
            raise ZendriverMCPError("proxy_url cannot be empty (use clear_proxy to disable)")
        session = self.session
        await session.stop()
        await session.start(headless=headless, user_data_dir=user_data_dir, proxy=proxy_url)
        return f"Browser restarted with proxy: {proxy_url}"

    async def clear_proxy(
        self,
        user_data_dir: str | None = None,
        headless: bool = False,
    ) -> str:
        """Restart the browser without a proxy."""
        session = self.session
        await session.stop()
        await session.start(headless=headless, user_data_dir=user_data_dir, proxy=None)
        return "Browser restarted without proxy"
