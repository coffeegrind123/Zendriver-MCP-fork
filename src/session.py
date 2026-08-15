# Browser session management for Zendriver MCP server.
# Includes CDP event listeners for network and console logging.

import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import zendriver as zd
from zendriver import cdp

from src.errors import BrowserNotStartedError, PageNotLoadedError

logger = logging.getLogger(__name__)

# A reset callback is invoked on every stop_browser to clear any
# session-bound state a tool was holding (interception handlers,
# accessibility uid tables, trace buffers, screencast handles, ...).
ResetCallback = Callable[[], None]


class BrowserSession:
    """Singleton class to manage browser session across all tool calls."""

    default_browser_path: str | None = None
    _instance: "BrowserSession | None" = None
    _browser: zd.Browser | None = None
    _page: zd.Tab | None = None
    _tabs: dict[str, zd.Tab] = {}
    _tab_counter: int = 0
    _network_logs: list[dict[str, Any]] = []
    _console_logs: list[dict[str, Any]] = []
    _pending_requests: dict[str, dict[str, Any]] = {}
    _cdp_enabled_tabs: dict[int, bool] = {}  # Track tabs with CDP listeners
    _proxy_username: str | None = None
    _proxy_password: str | None = None
    _loaded_extensions: list[dict[str, str]] = []
    _reset_callbacks: list[ResetCallback] = []

    def __new__(cls) -> "BrowserSession":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._tabs = {}
            cls._network_logs = []
            cls._console_logs = []
            cls._pending_requests = {}
            cls._cdp_enabled_tabs = {}
            cls._reset_callbacks = []
        return cls._instance

    def register_reset_callback(self, callback: ResetCallback) -> None:
        """Register a zero-arg callback that clears a tool's session state.

        Called once during ``stop_browser`` so the next session starts clean.
        Stateful tools register from their ``__init__`` (via ToolBase, which
        auto-registers any ``_reset_state`` method they define).
        """
        if callback not in self._reset_callbacks:
            self._reset_callbacks.append(callback)

    def _run_reset_callbacks(self) -> None:
        for cb in self._reset_callbacks:
            try:
                cb()
            except Exception:  # one bad tool shouldn't block the rest
                logger.exception("reset callback failed: %r", cb)

    @classmethod
    def get_instance(cls) -> "BrowserSession":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def browser(self) -> zd.Browser:
        """Get the browser instance."""
        if self._browser is None:
            raise BrowserNotStartedError()
        return self._browser

    @property
    def page(self) -> zd.Tab:
        """Get the current page/tab."""
        if self._page is None:
            raise PageNotLoadedError()
        return self._page

    @page.setter
    def page(self, value: zd.Tab) -> None:
        """Set the current page/tab."""
        self._page = value

    def is_browser_started(self) -> bool:
        """Check if browser is started."""
        return self._browser is not None

    def has_page(self) -> bool:
        """Check if a page is loaded."""
        return self._page is not None

    # Unpacked extensions auto-provisioned into extensions/<dir> on first launch.
    # asset_suffix picks the release asset; wrapper is the top-level directory
    # inside that zip, or None when the zip extracts flat (no wrapper dir).
    EXTENSIONS = (
        # uBlock Origin Lite, not uBlock Origin: gorhill/uBlock's .chromium.zip is
        # still manifest v2, and Chrome 150 refuses it outright ("Cannot install
        # extension because it uses an unsupported manifest version"), so the old
        # ublock/ dir could never have been blocking anything here. uBOL is the
        # MV3 build from the same project.
        {
            "dir": "ubol",
            "repo": "uBlockOrigin/uBOL-home",
            "asset_suffix": ".chromium.zip",
            "wrapper": None,
        },
        {
            "dir": "isdcac",
            "repo": "OhMyGuus/I-Still-Dont-Care-About-Cookies",
            "asset_suffix": "-chrome-source.zip",
            "wrapper": None,
        },
    )

    @staticmethod
    def _ensure_extension(spec: dict[str, Any]) -> str | None:
        """Download+unpack an extension if absent. Returns its dir, or None on failure.

        Extraction goes to a sibling temp dir that is renamed into place only once
        complete, so an interrupted download can never leave a half-populated
        directory that later launches would treat as already-installed.
        """
        import json
        import shutil
        import tempfile
        import urllib.request
        import zipfile

        ext_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extensions"
        )
        ext_dir = os.path.join(ext_root, spec["dir"])
        if os.path.isfile(os.path.join(ext_dir, "manifest.json")):
            return ext_dir
        if os.path.isdir(ext_dir):
            shutil.rmtree(ext_dir, ignore_errors=True)  # salvage a broken install

        os.makedirs(ext_root, exist_ok=True)
        staging = tempfile.mkdtemp(prefix=spec["dir"] + ".", dir=ext_root)
        zip_path = os.path.join(staging, "download.zip")
        try:
            api = "https://api.github.com/repos/%s/releases/latest" % spec["repo"]
            with urllib.request.urlopen(api, timeout=60) as r:
                meta = json.loads(r.read())
            url = next(
                a["browser_download_url"]
                for a in meta["assets"]
                if a["name"].endswith(spec["asset_suffix"])
            )
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(staging)
            os.remove(zip_path)

            src = staging if spec["wrapper"] is None else os.path.join(staging, spec["wrapper"])
            if not os.path.isfile(os.path.join(src, "manifest.json")):
                raise RuntimeError(
                    "%s: no manifest.json under %s (asset layout changed?)" % (spec["dir"], src)
                )
            os.rename(src, ext_dir)
            return ext_dir
        except Exception as e:
            # Never fail the launch over an extension — log loudly and carry on
            # unextended, but say WHICH one and why, so a silent no-adblock
            # session is not mistaken for a working one.
            print(
                "[zendriver-mcp] extension '%s' unavailable: %r" % (spec["dir"], e),
                file=sys.stderr,
                flush=True,
            )
            return None
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def _load_extensions(self, ext_dirs: list[str]) -> None:
        """Install the provisioned extensions over CDP, after the browser is up.

        Reports each install by name/id (and each failure with Chrome's own
        reason) so a session running without an adblocker is never mistaken for
        a working one.
        """
        if not ext_dirs:
            return
        for ext_dir in ext_dirs:
            try:
                ext_id = await self._browser.connection.send(  # type: ignore[union-attr]
                    cdp.extensions.load_unpacked(ext_dir)
                )
                self._loaded_extensions.append({"path": ext_dir, "id": ext_id})
            except Exception as e:
                print(
                    "[zendriver-mcp] extension load failed for %s: %s" % (ext_dir, e),
                    file=sys.stderr,
                    flush=True,
                )
        if self._loaded_extensions:
            print(
                "[zendriver-mcp] extensions loaded: %s"
                % ", ".join(
                    "%s (%s)" % (os.path.basename(x["path"]), x["id"])
                    for x in self._loaded_extensions
                ),
                file=sys.stderr,
                flush=True,
            )

    async def start(
        self,
        headless: bool = False,
        user_data_dir: str | None = None,
        proxy: str | None = None,
        browser_args: list[str] | None = None,
        browser_executable_path: str | None = None,
        low_memory: bool = False,
    ) -> zd.Browser:
        """Start the browser with configuration."""
        if self._browser is None:
            args = list(browser_args or [])

            # Opt-in only: these are needed on root/container hosts (tiny /dev/shm
            # crashes Chrome's renderer; no GPU under Xvfb) but they're DETECTABLE
            # as automation (software WebGL etc.), so they stay off by default and
            # the caller enables them per-launch via low_memory.
            if low_memory:
                for _a in (
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                ):
                    if _a not in args:
                        args.append(_a)

            # Extensions are NOT passed via --load-extension: Chrome 137+ ignores
            # that switch outright (verified on 150.0.7871.124 — even a one-file
            # MV3 test extension never appears, and the old
            # --disable-features=DisableLoadExtensionCommandLineSwitch escape
            # hatch no longer exists). The supported route is the CDP Extensions
            # domain, which needs this flag at launch; the actual install happens
            # in _load_extensions() once the browser is up.
            ext_dirs = [d for d in (self._ensure_extension(s) for s in self.EXTENSIONS) if d]
            if ext_dirs and "--enable-unsafe-extension-debugging" not in args:
                args.append("--enable-unsafe-extension-debugging")

            if proxy:
                parsed = urlparse(proxy)
                if parsed.username:
                    self._proxy_username = parsed.username
                    self._proxy_password = parsed.password or ""
                else:
                    self._proxy_username = None
                    self._proxy_password = None
                proxy_host = parsed.hostname or ""
                proxy_port = parsed.port or 8080
                scheme = parsed.scheme or "http"
                if scheme == "socks5":
                    args.append(f"--proxy-server=socks5://{proxy_host}:{proxy_port}")
                else:
                    args.append(f"--proxy-server={proxy_host}:{proxy_port}")

            exe = browser_executable_path or self.default_browser_path
            self._browser = await zd.start(
                headless=headless,
                user_data_dir=user_data_dir,
                browser_args=args if args else None,
                browser_executable_path=exe,
                sandbox=False,
                # Slow container Chrome can take several seconds to open the CDP
                # port; zendriver's default (0.25s x 10 = 2.5s) gives up too early.
                browser_connection_timeout=1.0,
                browser_connection_max_tries=40,
            )

            self._loaded_extensions = []
            await self._load_extensions(ext_dirs)

            # Clear state on new session
            self._network_logs = []
            self._console_logs = []
            self._pending_requests = {}
            self._tabs = {}
            self._tab_counter = 0

            # Harden zendriver's Transaction.__call__ against parser/enum version skew so a
            # CDP response that fails to parse (new Chrome enum value, etc.) resolves the
            # caller's future with a typed error instead of hanging it forever. This supersedes
            # the older InvalidStateError-only patch. See src/compat.py.
            from src import compat  # noqa: F401  (idempotent, applies patch on import)

            # Set up CDP listeners on initial tab (needed for proxy auth before first navigate)
            main_tab = self._browser.main_tab
            if main_tab:
                self._page = main_tab
                await self._setup_cdp_listeners(main_tab)
                self._tab_counter += 1
                self._tabs[f"tab_{self._tab_counter}"] = main_tab

        return self._browser

    async def stop(self) -> None:
        """Stop the browser and clean up."""
        if self._browser is not None:
            await self._browser.stop()
            self._browser = None
            self._page = None
            self._tabs = {}
            self._network_logs = []
            self._console_logs = []
            self._pending_requests = {}
            self._cdp_enabled_tabs = {}
            self._loaded_extensions = []
            self._run_reset_callbacks()

    async def _setup_cdp_listeners(self, tab: zd.Tab) -> None:
        """Set up CDP event listeners for network and console logging."""
        tab_id = id(tab)
        if self._cdp_enabled_tabs.get(tab_id):
            return  # Already set up

        try:
            # Enable Network domain
            await tab.send(cdp.network.enable())

            # Enable Runtime domain for console
            await tab.send(cdp.runtime.enable())

            # Enable Fetch domain for proxy auth if credentials are set
            if self._proxy_username:
                await tab.send(
                    cdp.fetch.enable(
                        handle_auth_requests=True,
                        patterns=[cdp.fetch.RequestPattern(url_pattern="*")],
                    )
                )
                if cdp.fetch not in tab.enabled_domains:
                    tab.enabled_domains.append(cdp.fetch)
                tab.add_handler(cdp.fetch.AuthRequired, self._on_fetch_auth_required)
                tab.add_handler(cdp.fetch.RequestPaused, self._on_fetch_request_paused)

            # Add event handlers
            tab.add_handler(cdp.network.RequestWillBeSent, self._on_request_sent)
            tab.add_handler(cdp.network.ResponseReceived, self._on_response_received)
            tab.add_handler(cdp.network.LoadingFailed, self._on_loading_failed)
            tab.add_handler(cdp.runtime.ConsoleAPICalled, self._on_console_api)

            # Mark tab as CDP-enabled
            self._cdp_enabled_tabs[tab_id] = True
        except Exception:
            # CDP not available or failed, continue without logging
            pass

    async def _on_request_sent(self, event: cdp.network.RequestWillBeSent) -> None:
        """Handle outgoing network requests."""
        request_id = str(event.request_id)
        self._pending_requests[request_id] = {
            "url": event.request.url,
            "method": event.request.method,
            "timestamp": datetime.now().isoformat(),
            "type": str(event.type_) if event.type_ else "unknown",
        }

    async def _on_response_received(self, event: cdp.network.ResponseReceived) -> None:
        """Handle network responses."""
        request_id = str(event.request_id)
        pending = self._pending_requests.pop(request_id, {})

        log_entry = {
            "url": event.response.url,
            "method": pending.get("method", "GET"),
            "status": event.response.status,
            "status_text": event.response.status_text,
            "type": str(event.type_) if event.type_ else pending.get("type", "unknown"),
            "mime_type": event.response.mime_type,
            "timestamp": datetime.now().isoformat(),
        }

        self._network_logs.append(log_entry)

        # Keep only last 1000 entries
        if len(self._network_logs) > 1000:
            self._network_logs = self._network_logs[-1000:]

    async def _on_loading_failed(self, event: cdp.network.LoadingFailed) -> None:
        """Handle failed network requests."""
        request_id = str(event.request_id)
        pending = self._pending_requests.pop(request_id, {})

        if pending:
            log_entry = {
                "url": pending.get("url", "unknown"),
                "method": pending.get("method", "GET"),
                "status": 0,
                "status_text": f"FAILED: {event.error_text}",
                "type": pending.get("type", "unknown"),
                "timestamp": datetime.now().isoformat(),
            }
            self._network_logs.append(log_entry)

    async def _on_console_api(self, event: cdp.runtime.ConsoleAPICalled) -> None:
        """Handle console API calls."""
        args_text = []
        for arg in event.args:
            if arg.value is not None:
                args_text.append(str(arg.value))
            elif arg.preview and arg.preview.properties:
                # serialize object properties for better display
                props = {p.name: p.value for p in arg.preview.properties if p.value}
                args_text.append(str(props) if props else arg.description or str(arg.type_))
            elif arg.description:
                args_text.append(arg.description)
            else:
                args_text.append(str(arg.type_))

        log_entry = {
            "type": str(event.type_),
            "text": " ".join(args_text),
            "timestamp": datetime.now().isoformat(),
        }

        self._console_logs.append(log_entry)

        # Keep only last 500 entries
        if len(self._console_logs) > 500:
            self._console_logs = self._console_logs[-500:]

    async def _on_fetch_request_paused(
        self, event: cdp.fetch.RequestPaused, connection=None
    ) -> None:
        """Continue paused requests (non-auth). Required when Fetch patterns are active."""
        try:
            target = connection or self._page
            if target:
                await target.send(cdp.fetch.continue_request(request_id=event.request_id))
        except Exception:
            pass

    async def _on_fetch_auth_required(self, event: cdp.fetch.AuthRequired, connection=None) -> None:
        """Handle proxy authentication challenges."""
        try:
            target = connection or self._page
            if target and self._proxy_username:
                await target.send(
                    cdp.fetch.continue_with_auth(
                        request_id=event.request_id,
                        auth_challenge_response=cdp.fetch.AuthChallengeResponse(
                            response="ProvideCredentials",
                            username=self._proxy_username,
                            password=self._proxy_password or "",
                        ),
                    )
                )
        except Exception:
            pass

    async def navigate(self, url: str, new_tab: bool = False) -> zd.Tab:
        """Navigate to a URL."""
        browser = self.browser
        if new_tab or self._page is None:
            self._page = await browser.get(url, new_tab=new_tab)
            # Set up CDP listeners for the new tab
            await self._setup_cdp_listeners(self._page)
            # Track the tab
            self._tab_counter += 1
            tab_id = f"tab_{self._tab_counter}"
            self._tabs[tab_id] = self._page
        else:
            # Check if CDP listeners need to be set up for existing page
            if not self._cdp_enabled_tabs.get(id(self._page)):
                await self._setup_cdp_listeners(self._page)
            await self._page.get(url)
        return self._page

    async def create_tab(self, url: str | None = None) -> tuple[str, zd.Tab]:
        """Create a new tab and return its ID."""
        browser = self.browser
        if url:
            tab = await browser.get(url, new_tab=True)
        else:
            tab = await browser.get("about:blank", new_tab=True)

        # Set up CDP listeners for the new tab
        await self._setup_cdp_listeners(tab)

        self._tab_counter += 1
        tab_id = f"tab_{self._tab_counter}"
        self._tabs[tab_id] = tab
        return tab_id, tab

    async def switch_tab(self, tab_id: str) -> zd.Tab:
        """Switch to a specific tab."""
        if tab_id not in self._tabs:
            raise ValueError(f"Tab not found: {tab_id}")
        self._page = self._tabs[tab_id]
        await self._page.bring_to_front()
        return self._page

    async def close_tab(self, tab_id: str) -> None:
        """Close a specific tab."""
        if tab_id not in self._tabs:
            raise ValueError(f"Tab not found: {tab_id}")
        tab = self._tabs[tab_id]
        await tab.close()
        del self._tabs[tab_id]

        # If closed tab was current, switch to another
        if self._page == tab:
            if self._tabs:
                self._page = list(self._tabs.values())[0]
            else:
                self._page = None

    def get_all_tabs(self) -> dict[str, str]:
        """Get all open tabs with their URLs."""
        result = {}
        for tab_id, tab in self._tabs.items():
            url = getattr(tab, "url", "unknown")
            result[tab_id] = url
        return result

    def get_network_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent network logs."""
        return self._network_logs[-limit:]

    def get_console_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent console logs."""
        return self._console_logs[-limit:]

    def clear_logs(self) -> None:
        """Clear all logs."""
        self._network_logs = []
        self._console_logs = []
        self._pending_requests = {}
