"""Structured response helper for MCP tools.

FastMCP accepts any JSON-serialisable return value. We expose a tiny
``ToolResponse`` builder so tools can return a consistent envelope with a
human-readable ``summary``, optional ``data`` payload, and any ``files`` we
wrote to disk.

Tools may still return plain strings where appropriate; this class is opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolResponse:
    """Envelope returned by structured tool handlers.

    ``summary`` is always present and is what simple clients should display.
    ``data`` holds JSON-serialisable extras. ``files`` lists absolute paths
    to artefacts the tool wrote (traces, screenshots, reports, ...).
    """

    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"summary": self.summary}
        if self.data:
            out["data"] = self.data
        if self.files:
            out["files"] = self.files
        return out
