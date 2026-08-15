"""Lighthouse wrapper: run the Lighthouse CLI against the active browser.

Lighthouse is a Node package; users must install it separately
(``npm i -g lighthouse``). The tool connects via the browser's existing
remote-debugging port, so we don't spin up a second browser.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from src.artifacts import resolve_artifact_path
from src.errors import LighthouseNotInstalledError, ZendriverMCPError
from src.tools.base import ToolBase

_DEFAULT_CATEGORIES = [
    "performance",
    "accessibility",
    "best-practices",
    "seo",
]


class LighthouseTools(ToolBase):
    """Run Lighthouse audits against the current tab."""

    def _register_tools(self) -> None:
        # Full Lighthouse audits routinely take 60-120s; allow up to 5 min.
        self._register(self.run_lighthouse, timeout=300)
        self._register(self.check_lighthouse_available, timeout=10)

    async def check_lighthouse_available(self) -> dict[str, Any]:
        """Report whether the ``lighthouse`` CLI is installed and its version.

        Use this before ``run_lighthouse`` if you want to prompt the user to
        install it.
        """
        binary = shutil.which("lighthouse")
        if not binary:
            return {
                "available": False,
                "hint": "Install with `npm i -g lighthouse` (requires Node).",
            }
        proc = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        return {"available": True, "path": binary, "version": out.decode().strip()}

    async def run_lighthouse(
        self,
        url: str,
        categories: list[str] | None = None,
        form_factor: str = "mobile",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Audit ``url`` with Lighthouse and return scores + report path.

        - ``categories``: subset of ``performance``, ``accessibility``,
          ``best-practices``, ``seo``, ``pwa``. Defaults to the first four.
        - ``form_factor``: ``"mobile"`` or ``"desktop"``.
        - ``output_path``: where to save the JSON report. Empty => temp file.

        The browser must have been started with remote-debugging exposed,
        which zendriver does by default.
        """
        binary = shutil.which("lighthouse")
        if not binary:
            raise LighthouseNotInstalledError

        port = self._extract_debug_port()
        if port is None:
            raise ZendriverMCPError("Could not determine the browser's remote debugging port.")

        report_target = resolve_artifact_path(
            output_path, default_prefix="lighthouse", default_ext="json"
        )

        args = [
            binary,
            url,
            f"--port={port}",
            "--output=json",
            f"--output-path={report_target}",
            f"--form-factor={form_factor}",
            "--only-categories=" + ",".join(categories or _DEFAULT_CATEGORIES),
            "--quiet",
            # We connect to a running browser via --port; no need for
            # --chrome-flags (Lighthouse would otherwise try to spawn a
            # second Chrome despite the port attachment).
        ]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise ZendriverMCPError(
                f"Lighthouse failed ({proc.returncode}): {stderr.decode()[:500]}"
            )

        report = json.loads(report_target.read_text())
        scores = {
            name: round((entry.get("score") or 0) * 100)
            for name, entry in report.get("categories", {}).items()
        }
        return {
            "url": url,
            "form_factor": form_factor,
            "scores": scores,
            "report_path": str(report_target),
        }

    def _extract_debug_port(self) -> int | None:
        """Find the remote debugging port the browser is listening on.

        Zendriver keeps the port on the websocket URL used by the primary
        connection; we fall back to parsing the browser's stored config when
        that isn't exposed.
        """
        browser = self.session.browser
        ws_url = getattr(browser.connection, "websocket_url", None) or getattr(
            browser, "websocket_url", None
        )
        if isinstance(ws_url, str) and ":" in ws_url:
            try:
                return int(ws_url.rsplit(":", 1)[-1].split("/", 1)[0])
            except ValueError:
                return None
        config_port = getattr(getattr(browser, "config", None), "port", None)
        if isinstance(config_port, int):
            return config_port
        return None
