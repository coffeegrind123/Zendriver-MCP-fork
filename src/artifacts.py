"""Path sandboxing for tools that write artefacts to disk.

Tools like ``export_cookies``, ``stop_trace``, ``take_heap_snapshot``,
``screenshot`` and ``start_screencast`` accept file paths from the MCP
client (an LLM agent). Without a guard, a buggy or adversarial agent
could overwrite files like ``~/.ssh/authorized_keys``.

This module provides :func:`resolve_artifact_path` which:

1. Auto-generates a timestamped path under the system temp dir when the
   caller passes an empty string (the previous default behaviour).
2. Resolves user-supplied paths, then refuses them unless the target is
   inside the user's home directory, the system temp dir, or an extra
   directory listed in the ``ZENDRIVER_MCP_ARTIFACT_ROOT`` env var.

The goal is a light sandbox, not a security boundary: a tool running as
the user can always be tricked into writing user-writable files, but
``/etc``, ``/usr``, ``/System``, and other people's home dirs are firmly
out of bounds by default.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from src.errors import ZendriverMCPError


def _allowed_roots() -> list[Path]:
    roots = [Path.home().resolve(), Path(tempfile.gettempdir()).resolve()]
    extra = os.environ.get("ZENDRIVER_MCP_ARTIFACT_ROOT")
    if extra:
        for raw in extra.split(os.pathsep):
            raw = raw.strip()
            if raw:
                try:
                    roots.append(Path(raw).expanduser().resolve())
                except OSError:
                    continue
    return roots


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def resolve_artifact_path(
    file_path: str,
    *,
    default_prefix: str,
    default_ext: str,
) -> Path:
    """Translate a user-supplied ``file_path`` into a resolved ``Path``.

    - Empty ``file_path`` -> ``$TMPDIR/<default_prefix>-<timestamp>.<default_ext>``.
    - Non-empty ``file_path`` -> expanduser + resolve. Must fall inside
      one of :func:`_allowed_roots` (``$HOME``, the system temp dir, and
      anything in ``ZENDRIVER_MCP_ARTIFACT_ROOT``).

    Raises ``ZendriverMCPError`` when the path is outside every allowed
    root. The parent directory is created to avoid the caller having to
    pre-`mkdir`.
    """
    if not file_path:
        stem = f"{default_prefix}-{int(time.time())}.{default_ext}"
        target = Path(tempfile.gettempdir()) / stem
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    target = Path(file_path).expanduser()
    # Resolve even if the final component doesn't exist yet.
    resolved = target.resolve()
    for root in _allowed_roots():
        if _is_under(resolved, root):
            resolved.parent.mkdir(parents=True, exist_ok=True)
            return resolved

    raise ZendriverMCPError(
        f"Refusing to write to {resolved}: it's outside the allowed roots "
        f"({', '.join(str(r) for r in _allowed_roots())}). "
        "Pass a path under $HOME or the temp dir, or add a directory to "
        "the ZENDRIVER_MCP_ARTIFACT_ROOT environment variable."
    )


def resolve_artifact_dir(file_path: str, *, default_prefix: str) -> Path:
    """Same sandbox as :func:`resolve_artifact_path` but returns a directory.

    Used by tools that need a directory target (``start_screencast``).
    The directory is created if it doesn't exist.
    """
    if not file_path:
        target = Path(tempfile.mkdtemp(prefix=f"{default_prefix}-"))
        return target

    target = Path(file_path).expanduser().resolve()
    for root in _allowed_roots():
        if _is_under(target, root):
            target.mkdir(parents=True, exist_ok=True)
            return target

    raise ZendriverMCPError(
        f"Refusing to use directory {target}: outside allowed roots "
        f"({', '.join(str(r) for r in _allowed_roots())})."
    )
