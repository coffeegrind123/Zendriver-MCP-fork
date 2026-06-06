# browser lifecycle tools - start, stop, status
from typing import Optional

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
        headless: bool = False,
        proxy: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        low_memory: bool = False,
        window_size: Optional[str] = None,
        device_scale_factor: Optional[float] = None
    ) -> str:
        """Start the browser with configuration options.

        low_memory: opt-in Chrome flags for constrained/containerised hosts
            (--disable-dev-shm-usage for tiny /dev/shm, --disable-gpu, and
            first-run skips). REQUIRED on root/Docker envs with a small
            /dev/shm or no GPU, where the browser otherwise fails to connect.
            WARNING: these flags are detectable as automation (software WebGL,
            etc.) — leave False (default) when stealth matters (anti-bot /
            Cloudflare-protected sites). The agent decides per launch.
        window_size: viewport size as "WIDTHxHEIGHT" (e.g. "1440x900") or
            "WIDTH,HEIGHT". Without it Chrome uses its headless default
            (~800x600), which is why screenshots come out small. Set this for
            larger captures. Applied as the launch --window-size flag.
        device_scale_factor: device pixel ratio (e.g. 2 for retina-quality,
            crisp screenshots). Applied as --force-device-scale-factor; the
            captured image is window_size * device_scale_factor pixels.
        """
        browser_args = []
        if window_size:
            w, _, h = window_size.replace(",", "x").partition("x")
            try:
                browser_args.append(f"--window-size={int(w.strip())},{int(h.strip())}")
            except ValueError:
                raise ValueError(
                    f"Invalid window_size {window_size!r}; expected 'WIDTHxHEIGHT' (e.g. '1440x900')"
                )
        if device_scale_factor:
            browser_args.append(f"--force-device-scale-factor={device_scale_factor}")

        await self.session.start(
            headless=headless, proxy=proxy, user_data_dir=user_data_dir,
            low_memory=low_memory,
            browser_args=browser_args or None
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
        """Stop the browser and clean up all resources."""
        await self.session.stop()
        return "Browser stopped and all resources cleaned up"

    async def get_browser_status(self) -> str:
        """Get current browser status and session info."""
        if not self.session.is_browser_started():
            return "Browser: Not started"

        # list all open tabs
        tabs = self.session.get_all_tabs()
        lines = [f"Browser: Running", f"Open tabs: {len(tabs)}"]
        for tab_id, url in tabs.items():
            lines.append(f"  - {tab_id}: {url}")
        return "\n".join(lines)
