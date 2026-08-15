# browser lifecycle tools - start, stop, status
from typing import Annotated

from pydantic import Field

from src.tools.base import ToolBase


class BrowserTools(ToolBase):
    """tools for browser lifecycle management"""

    def _register_tools(self) -> None:
        """register browser lifecycle tools"""
        self._mcp.tool()(self.start_browser)
        self._mcp.tool()(self.stop_browser)
        self._mcp.tool()(self.get_browser_status)

    async def start_browser(
        self,
        headless: Annotated[
            bool,
            Field(
                description="Run with no visible window. Default False (headed). Headed is what actually beats Cloudflare and most bot detection — prefer False on protected sites. Example: false"
            ),
        ] = False,
        proxy: Annotated[
            str | None,
            Field(
                description="Proxy URL, with optional inline credentials. Omit for a direct connection. Example: 'http://user:pass@10.0.0.1:8080'"
            ),
        ] = None,
        user_data_dir: Annotated[
            str | None,
            Field(
                description="Absolute path to a Chrome profile directory, to persist cookies and logins across runs. Omit for a fresh temporary profile. Example: '/home/user/.cache/zendriver-profile'"
            ),
        ] = None,
        low_memory: Annotated[
            bool,
            Field(
                description="Opt-in Chrome flags for constrained or containerised hosts (--disable-dev-shm-usage, --disable-gpu, first-run skips). REQUIRED on root/Docker hosts with a small /dev/shm or no GPU, where the browser otherwise fails to connect. WARNING: detectable as automation (software WebGL) — leave false when stealth matters. Example: false"
            ),
        ] = False,
        window_size: Annotated[
            str | None,
            Field(
                description="Viewport size as 'WIDTHxHEIGHT' or 'WIDTH,HEIGHT'. Omitted, Chrome uses its headless default of roughly 800x600, which is why screenshots come out small. Example: '1440x900'"
            ),
        ] = None,
        device_scale_factor: Annotated[
            float | None,
            Field(
                description="Device pixel ratio, for retina-quality captures. The screenshot is window_size * device_scale_factor pixels. Example: 2"
            ),
        ] = None,
    ) -> str:
        """Launch the browser and start a session. Required before every other tool.

        Every other tool in this server operates on the browser this starts, and
        fails without it. One browser at a time; call stop_browser before starting
        another with different options. Returns a confirmation naming the mode and
        any options applied.
        """
        browser_args = []
        if window_size:
            w, _, h = window_size.replace(",", "x").partition("x")
            try:
                browser_args.append(f"--window-size={int(w.strip())},{int(h.strip())}")
            except ValueError as exc:
                raise ValueError(
                    f"Invalid window_size {window_size!r}; expected 'WIDTHxHEIGHT' (e.g. '1440x900')"
                ) from exc
        if device_scale_factor:
            browser_args.append(f"--force-device-scale-factor={device_scale_factor}")

        await self.session.start(
            headless=headless,
            proxy=proxy,
            user_data_dir=user_data_dir,
            low_memory=low_memory,
            browser_args=browser_args or None,
        )

        # build response message
        mode = "headless" if headless else "headed"
        extras = []
        if proxy:
            extras.append(f"proxy={proxy}")
        if user_data_dir:
            extras.append(f"profile={user_data_dir}")
        if low_memory:
            extras.append("low_memory")
        if window_size:
            extras.append(f"window={window_size}")
        if device_scale_factor:
            extras.append(f"scale={device_scale_factor}")
        extra_info = f" ({', '.join(extras)})" if extras else ""
        return f"Browser started in {mode} mode{extra_info}"

    async def stop_browser(self) -> str:
        """Stop the browser, close every tab, and release all session resources.

        Call when finished, or before start_browser to relaunch with different
        options. Unsaved page state and non-persisted cookies are lost. Returns a
        confirmation string.
        """
        await self.session.stop()
        return "Browser stopped and all resources cleaned up"

    async def get_browser_status(self) -> str:
        """Check whether the browser is running, and list its open tabs.

        Use to decide whether start_browser is still needed, or to recover tab ids
        after losing track. Returns 'Browser: Not started', or 'Browser: Running'
        followed by the tab count and one '  - <tab_id>: <url>' line per tab.
        """
        if not self.session.is_browser_started():
            return "Browser: Not started"

        # list all open tabs
        tabs = self.session.get_all_tabs()
        lines = ["Browser: Running", f"Open tabs: {len(tabs)}"]
        for tab_id, url in tabs.items():
            lines.append(f"  - {tab_id}: {url}")
        return "\n".join(lines)
