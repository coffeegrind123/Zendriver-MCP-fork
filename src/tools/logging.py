# logging tools - network and console log management
import time
from typing import Annotated

from pydantic import Field

from src.tools.base import ToolBase


class LoggingTools(ToolBase):
    """tools for network and console log access"""

    def _register_tools(self) -> None:
        """register logging tools"""
        self._mcp.tool()(self.get_network_logs)
        self._mcp.tool()(self.get_console_logs)
        self._mcp.tool()(self.clear_logs)
        self._mcp.tool()(self.wait_for_network)
        self._mcp.tool()(self.wait_for_request)

    async def get_network_logs(
        self,
        limit: Annotated[
            int,
            Field(
                description="Maximum number of most-recent requests to return. Raise it to see more history. Example: 20"
            ),
        ] = 20,
    ) -> str:
        """List recent network requests the page made, captured via CDP.

        Use to find the API endpoint behind a rendered value, or to confirm a
        request fired and what it returned. Capture starts when the browser does,
        so requests made before start_browser are absent. Returns one
        '  METHOD url status type' line per request, URLs truncated to 80
        characters, or 'No network logs captured'.
        """
        logs = self.session.get_network_logs(limit)
        if not logs:
            return "No network logs captured"

        lines = [f"Network logs ({len(logs)} entries):"]
        for log in logs:
            method = log.get("method", "GET")
            url = log.get("url", "unknown")[:80]
            status = log.get("status", "?")
            rtype = log.get("type", "")
            lines.append(f"  {method} {url} {status} {rtype}".rstrip())
        return "\n".join(lines)

    async def get_console_logs(
        self,
        limit: Annotated[
            int,
            Field(
                description="Maximum number of most-recent console entries to return. Raise it to see more history. Example: 20"
            ),
        ] = 20,
    ) -> str:
        """List recent browser console output, captured via CDP.

        Use to surface JavaScript errors explaining why a page rendered wrong or
        an interaction did nothing. Returns one '  [type] text' line per entry,
        where type is log, warn, error, or similar, each truncated to 100
        characters, or 'No console logs captured'.
        """
        logs = self.session.get_console_logs(limit)
        if not logs:
            return "No console logs captured"

        lines = [f"Console logs ({len(logs)} entries):"]
        for log in logs:
            log_type = log.get("type", "log")
            text = log.get("text", "")[:100]
            lines.append(f"  [{log_type}] {text}")
        return "\n".join(lines)

    async def clear_logs(self) -> str:
        """Discard all captured network and console logs.

        Call before an action so the logs that follow contain only that action's
        traffic — much easier to read than filtering a long history. Returns a
        confirmation string.
        """
        self.session.clear_logs()
        return "Cleared all logs"

    async def wait_for_network(
        self,
        timeout: Annotated[
            float, Field(description="Maximum seconds to wait before giving up. Example: 10.0")
        ] = 10.0,
        idle_time: Annotated[
            float,
            Field(
                description="Seconds with no new request before the network counts as idle. Example: 0.5"
            ),
        ] = 0.5,
    ) -> str:
        """Wait until the page stops making network requests.

        Use after a click or navigation that triggers API calls, so later reads
        see the settled page. Use wait_for_request instead when you know which
        request matters. Returns how long it took plus the request count, or a
        timeout message — a timeout is not an error, just an unsettled page.
        """
        start = time.time()
        last_count = 0
        idle_start = None

        while time.time() - start < timeout:
            logs = self.session.get_network_logs(100)
            current_count = len(logs)

            if current_count == last_count:
                # no new requests
                if idle_start is None:
                    idle_start = time.time()
                elif time.time() - idle_start >= idle_time:
                    # network has been idle long enough
                    elapsed = time.time() - start
                    return f"Network idle after {elapsed:.1f}s ({current_count} requests captured)"
            else:
                # new request came in, reset idle timer
                idle_start = None
                last_count = current_count

            await self.session.page.wait(0.1)

        return f"Timeout after {timeout}s - network may still be active ({last_count} requests captured)"

    async def wait_for_request(
        self,
        url_pattern: Annotated[
            str,
            Field(
                description="Case-insensitive substring matched anywhere in the request URL. Not a regex and not a glob. Example: '/api/search'"
            ),
        ],
        timeout: Annotated[
            float, Field(description="Maximum seconds to wait before giving up. Example: 30.0")
        ] = 30.0,
        method: Annotated[
            str | None,
            Field(
                description="HTTP method the request must use, matched case-insensitively. Omit to accept any method. Example: 'POST'"
            ),
        ] = None,
    ) -> str:
        """Wait until one specific network request appears, matched by URL substring.

        More precise than wait_for_network when you know the endpoint that
        matters — a search API, a GraphQL call. Returns the matched request's
        method, URL, status, and type plus how long it took, or a timeout message
        naming the pattern that never matched.
        """
        start = time.time()
        seen_requests = set()
        safe_pattern = url_pattern.lower()
        safe_method = method.upper() if method else None

        while time.time() - start < timeout:
            logs = self.session.get_network_logs(200)

            for log in logs:
                # create unique id for this request
                req_id = f"{log.get('method', '')}:{log.get('url', '')}:{log.get('timestamp', '')}"
                if req_id in seen_requests:
                    continue
                seen_requests.add(req_id)

                url = log.get("url", "").lower()
                req_method = log.get("method", "GET")

                # check if this request matches our pattern
                if safe_pattern in url:
                    if safe_method and req_method != safe_method:
                        continue

                    status = log.get("status", "?")
                    elapsed = time.time() - start
                    return (
                        f"Found matching request after {elapsed:.1f}s:\n"
                        f"  {req_method} {log.get('url', '')[:100]}\n"
                        f"  Status: {status}\n"
                        f"  Type: {log.get('type', 'unknown')}"
                    )

            await self.session.page.wait(0.2)

        return f"Timeout: No request matching '{url_pattern}' found after {timeout}s"
