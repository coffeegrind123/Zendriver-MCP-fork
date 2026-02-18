# Runner script for Zendriver MCP server
import argparse
import sys
import os

# Add the project directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.tools import mcp
from src.session import BrowserSession

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-path", help="Path to browser executable")
    args = parser.parse_args()

    if args.browser_path:
        BrowserSession.default_browser_path = args.browser_path

    mcp.run(transport="stdio")
