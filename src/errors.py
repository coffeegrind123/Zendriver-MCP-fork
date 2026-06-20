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


class CloudflareChallengeError(ZendriverMCPError):
    # raised when a Cloudflare interactive (Turnstile) challenge could not be solved
    def __init__(self, message: str = "Could not solve the Cloudflare challenge in time."):
        super().__init__(message)
