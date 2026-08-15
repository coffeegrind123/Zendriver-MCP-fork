"""Permission overrides for the browser context.

Pre-grant or deny the things Chrome usually asks about: geolocation, camera,
microphone, notifications, clipboard. Avoids agents getting stuck on a
"Do you want to allow..." prompt.
"""

from __future__ import annotations

from zendriver import cdp

from src.errors import ZendriverMCPError
from src.tools.base import ToolBase

# The full enum has 30+ entries; these are the ones agents care about.
_COMMON_PERMISSIONS = {
    "geolocation": cdp.browser.PermissionType.GEOLOCATION,
    "notifications": cdp.browser.PermissionType.NOTIFICATIONS,
    "camera": cdp.browser.PermissionType.VIDEO_CAPTURE,
    "microphone": cdp.browser.PermissionType.AUDIO_CAPTURE,
    "clipboard-read-write": cdp.browser.PermissionType.CLIPBOARD_READ_WRITE,
    "midi": cdp.browser.PermissionType.MIDI,
    "midi-sysex": cdp.browser.PermissionType.MIDI_SYSEX,
    "background-sync": cdp.browser.PermissionType.BACKGROUND_SYNC,
    "storage-access": cdp.browser.PermissionType.STORAGE_ACCESS,
    "local-fonts": cdp.browser.PermissionType.LOCAL_FONTS,
}


class PermissionsTools(ToolBase):
    """Grant or reset browser permissions for an origin."""

    def _register_tools(self) -> None:
        self._register(self.grant_permissions)
        self._register(self.reset_permissions)
        self._register(self.list_permission_names)

    async def grant_permissions(self, permissions: list[str], origin: str | None = None) -> str:
        """Grant ``permissions`` to ``origin`` (or all origins if omitted).

        Accepted names are listed by ``list_permission_names``. Any name
        that resolves to a full CDP enum key is also accepted (e.g.
        ``VIDEO_CAPTURE`` or ``videoCapture``).
        """
        resolved: list[cdp.browser.PermissionType] = []
        unknown: list[str] = []
        for name in permissions:
            lowered = name.strip().lower()
            if lowered in _COMMON_PERMISSIONS:
                resolved.append(_COMMON_PERMISSIONS[lowered])
                continue
            try:
                resolved.append(cdp.browser.PermissionType.from_json(name))
            except ValueError:
                unknown.append(name)

        if unknown:
            raise ZendriverMCPError(f"Unknown permissions: {unknown}. Try list_permission_names().")

        connection = self.session.browser.connection
        if connection is None:
            raise ZendriverMCPError("Browser has no active connection")
        await connection.send(cdp.browser.grant_permissions(permissions=resolved, origin=origin))
        scope = origin or "all origins"
        return f"Granted {len(resolved)} permission(s) to {scope}"

    async def reset_permissions(self) -> str:
        """Reset every permission override in the current browser context."""
        connection = self.session.browser.connection
        if connection is None:
            raise ZendriverMCPError("Browser has no active connection")
        await connection.send(cdp.browser.reset_permissions())
        return "Permissions reset to defaults"

    async def list_permission_names(self) -> list[str]:
        """Return the short permission names we accept in ``grant_permissions``."""
        return sorted(_COMMON_PERMISSIONS.keys())
