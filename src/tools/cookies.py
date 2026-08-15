"""Cookie import/export via CDP (works on HTTP-only cookies, all domains).

Upstream ``StorageTools.get_cookies`` reads ``document.cookie`` - that misses
HTTP-only cookies and only sees the current origin. These tools use
``Network.getAllCookies`` / ``Network.setCookies`` which see everything the
browser stores, so you can round-trip a full authenticated session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zendriver import cdp

from src.artifacts import resolve_artifact_path
from src.errors import ZendriverMCPError
from src.response import ToolResponse
from src.tools.base import ToolBase


class CookieTools(ToolBase):
    """Full-fidelity cookie I/O, including HTTP-only cookies across all origins."""

    def _register_tools(self) -> None:
        self._register(self.export_cookies)
        self._register(self.import_cookies)
        self._register(self.clear_all_cookies)
        self._register(self.list_all_cookies)

    async def list_all_cookies(self) -> list[dict[str, Any]]:
        """Return every cookie the browser is holding, across all origins."""
        tab = self.session.page
        cookies = await tab.send(cdp.network.get_all_cookies())
        return [c.to_json() for c in cookies or []]

    async def export_cookies(self, file_path: str) -> dict[str, Any]:
        """Dump all cookies to a JSON file.

        Compatible with the "Edit This Cookie" Chrome extension export format
        and Playwright's storage state shape (just the cookies slice).
        Use ``import_cookies`` on another session to restore.
        """
        if not file_path:
            raise ZendriverMCPError("file_path is required")

        tab = self.session.page
        cookies = await tab.send(cdp.network.get_all_cookies()) or []
        serialised = [c.to_json() for c in cookies]

        target = resolve_artifact_path(
            file_path, default_prefix="zendriver-cookies", default_ext="json"
        )
        target.write_text(json.dumps(serialised, indent=2, sort_keys=True))
        # Cookies contain session secrets; restrict to owner-read/write.
        try:
            target.chmod(0o600)
        except OSError:
            pass  # best-effort on platforms without POSIX perms
        return ToolResponse(
            summary=f"Exported {len(serialised)} cookies -> {target}",
            data={"count": len(serialised)},
            files=[str(target)],
        ).to_dict()

    async def import_cookies(self, file_path: str) -> str:
        """Load cookies from a JSON file produced by ``export_cookies``.

        Silently skips entries that fail the ``CookieParam`` schema so a
        partial restore still works. Existing cookies with the same
        (name, domain, path) triple are overwritten by Chrome.
        """
        source = Path(file_path).expanduser().resolve()
        if not source.exists():
            raise ZendriverMCPError(f"Cookie file not found: {source}")

        raw = json.loads(source.read_text())
        if not isinstance(raw, list):
            raise ZendriverMCPError("Cookie file must contain a JSON array")

        params: list[cdp.network.CookieParam] = []
        skipped = 0
        for entry in raw:
            try:
                params.append(cdp.network.CookieParam.from_json(entry))
            except (KeyError, ValueError, TypeError):
                skipped += 1

        if not params:
            raise ZendriverMCPError("No valid cookies found in file")

        await self.session.page.send(cdp.network.set_cookies(cookies=params))
        suffix = f" ({skipped} skipped)" if skipped else ""
        return f"Imported {len(params)} cookies{suffix}"

    async def clear_all_cookies(self) -> str:
        """Delete every cookie in the current browser context."""
        await self.session.page.send(cdp.network.clear_browser_cookies())
        return "All cookies cleared"
