# custom exceptions for Zendriver MCP server


class ZendriverMCPError(Exception):
    # base exception for all Zendriver MCP errors
    pass


class BrowserNotStartedError(ZendriverMCPError):
    # raised when browser operations attempted before starting
    def __init__(self, message: str = "Browser not started. Call start_browser first."):
        super().__init__(message)


class PageNotLoadedError(ZendriverMCPError):
    # raised when page operations attempted before navigating
    def __init__(self, message: str = "No page loaded. Navigate to a URL first."):
        super().__init__(message)


class ElementNotFoundError(ZendriverMCPError):
    # raised when element cannot be found on the page
    def __init__(self, selector: str):
        super().__init__(f"Element not found: {selector}")
        self.selector = selector


class ToolTimeoutError(ZendriverMCPError):
    # raised when a tool exceeds its time budget (prevents a hung CDP call from
    # freezing the whole MCP session forever)
    def __init__(self, tool: str, timeout: float):
        super().__init__(f"Tool '{tool}' exceeded its {timeout:.0f}s time budget")
        self.tool = tool
        self.timeout = timeout


class BrowserUnreachableError(ZendriverMCPError):
    # raised when a tool's budget ran out AND Chrome no longer answers CDP, i.e.
    # the browser is alive but wedged. Distinct from ToolTimeoutError, which says
    # only "this call was slow" and is what a 22-hour outage reported instead.
    def __init__(self, tool: str, timeout: float, will_autostart: bool):
        nxt = (
            "The next call starts a fresh one."
            if will_autostart
            else "Call start_browser for a fresh one."
        )
        super().__init__(
            f"Tool '{tool}' hit its {timeout:.0f}s budget and Chrome stopped answering "
            f"CDP — the process was still alive but wedged, so the dead session has "
            f"been discarded. {nxt}"
        )
        self.tool = tool
        self.timeout = timeout


class CloudflareChallengeError(ZendriverMCPError):
    # raised when a Cloudflare interactive (Turnstile) challenge could not be solved
    def __init__(self, message: str = "Could not solve the Cloudflare challenge in time."):
        super().__init__(message)


class TracingError(ZendriverMCPError):
    # raised on unexpected state transitions around CDP Tracing.* commands
    pass


class LighthouseNotInstalledError(ZendriverMCPError):
    # raised when the `lighthouse` CLI is missing on the PATH
    def __init__(self) -> None:
        super().__init__("Lighthouse CLI not found. Install with `npm i -g lighthouse`.")


class AccessibilityUidError(ZendriverMCPError):
    # raised when a caller references an unknown or stale accessibility uid
    pass


class BrowserLaunchError(ZendriverMCPError):
    """Chrome was asked to start, tried, and died — with Chrome's own reason.

    Distinct from ``BrowserNotStartedError`` ("nobody called start_browser")
    and from ``ToolTimeoutError`` ("the tool ran too long"). Those two used to
    absorb every launch failure between them, which is how a dead ``$DISPLAY``
    reached a caller as a bare transport timeout: the one line that named the
    cause went to a log file and the exception guessed at something else.
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
