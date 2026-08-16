# Runner script for Zendriver MCP server
import argparse
import os
import sys

# Add the project directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.session import BrowserSession
from src.tools import mcp

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-path", help="Path to browser executable")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help=(
            "stdio (default) spawns one server per client, so the browser dies with the "
            "client. streamable-http keeps ONE long-lived server that many clients — or "
            "many separate CLI invocations — share, which is what a stateful browser needs."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8931, help="HTTP port")
    parser.add_argument(
        "--stateful",
        action="store_true",
        help=(
            "Keep per-client MCP sessions (requires the initialize handshake on every "
            "connection). Default is stateless: any client can POST a single tools/call "
            "and get an answer, which is what makes a shell wrapper practical. The BROWSER "
            "is process-global either way — stateless refers to the MCP session, not the tab."
        ),
    )
    args = parser.parse_args()

    if args.browser_path:
        BrowserSession.default_browser_path = args.browser_path

    if args.transport != "stdio":
        # Set on the instance rather than through FASTMCP_HOST / FASTMCP_PORT.
        # Those env vars are documented by FastMCP but are NOT read in mcp SDK
        # >= 1.28: FastMCP.__init__ passes its own keyword defaults straight to
        # Settings, so the environment is bypassed. Measured 2026-08-16 — with
        # FASTMCP_PORT=8931 exported, the server bound 8000 and said so.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.settings.stateless_http = not args.stateful
        # Plain JSON instead of an SSE frame per response: every HTTP client can
        # read it, including curl and the stdlib.
        mcp.settings.json_response = True

    mcp.run(transport=args.transport)
