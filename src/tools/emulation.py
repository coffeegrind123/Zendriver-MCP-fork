"""Emulation tools: viewport, device profiles, CPU + network throttling.

All state is per-tab and resets on tab close. Pass `0` widths/heights or
``restore_*`` tools to revert to the real device.
"""

from __future__ import annotations

from dataclasses import dataclass

from zendriver import cdp

from src.tools.base import ToolBase


@dataclass(frozen=True)
class DeviceProfile:
    """Minimal device emulation profile."""

    name: str
    width: int
    height: int
    device_scale_factor: float
    mobile: bool
    user_agent: str


# Small but representative set - matches DevTools' preset dropdown closely.
DEVICE_PROFILES: dict[str, DeviceProfile] = {
    "iphone-15-pro": DeviceProfile(
        name="iPhone 15 Pro",
        width=393,
        height=852,
        device_scale_factor=3.0,
        mobile=True,
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
    ),
    "pixel-8": DeviceProfile(
        name="Pixel 8",
        width=412,
        height=915,
        device_scale_factor=2.625,
        mobile=True,
        user_agent=(
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Mobile Safari/537.36"
        ),
    ),
    "ipad-pro": DeviceProfile(
        name="iPad Pro 12.9",
        width=1024,
        height=1366,
        device_scale_factor=2.0,
        mobile=True,
        user_agent=(
            "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
    ),
    "desktop-1080p": DeviceProfile(
        name="Desktop 1080p",
        width=1920,
        height=1080,
        device_scale_factor=1.0,
        mobile=False,
        user_agent="",  # empty => don't override
    ),
}


# Matches the throttling presets DevTools exposes.
NETWORK_PROFILES: dict[str, dict[str, float]] = {
    # latency ms, down Bps, up Bps
    "offline": {"offline": 1, "latency": 0, "download": 0, "upload": 0},
    "slow-3g": {
        "offline": 0,
        "latency": 400,
        "download": 400 * 1024 / 8,
        "upload": 400 * 1024 / 8,
    },
    "fast-3g": {
        "offline": 0,
        "latency": 150,
        "download": 1.5 * 1024 * 1024 / 8,
        "upload": 750 * 1024 / 8,
    },
    "4g": {
        "offline": 0,
        "latency": 20,
        "download": 9 * 1024 * 1024 / 8,
        "upload": 1.5 * 1024 * 1024 / 8,
    },
    "no-throttling": {"offline": 0, "latency": 0, "download": -1, "upload": -1},
}


class EmulationTools(ToolBase):
    """Viewport, device, CPU, and network emulation via CDP."""

    def _register_tools(self) -> None:
        self._register(self.set_viewport)
        self._register(self.restore_viewport)
        self._register(self.set_device)
        self._register(self.list_devices)
        self._register(self.set_cpu_throttle)
        self._register(self.set_network_conditions)
        self._register(self.list_network_profiles)
        self._register(self.emulate_media)

    async def set_viewport(
        self,
        width: int,
        height: int,
        device_scale_factor: float = 1.0,
        mobile: bool = False,
    ) -> str:
        """Override the visible viewport dimensions.

        ``width`` / ``height`` in CSS pixels. ``device_scale_factor`` is DPR
        (1.0 for standard, 2.0 for Retina, etc.). ``mobile`` toggles the
        meta-viewport / overlay-scrollbar behaviour.
        """
        await self.session.page.send(
            cdp.emulation.set_device_metrics_override(
                width=width,
                height=height,
                device_scale_factor=device_scale_factor,
                mobile=mobile,
            )
        )
        return f"Viewport: {width}x{height}@{device_scale_factor}x (mobile={mobile})"

    async def restore_viewport(self) -> str:
        """Restore the real device's viewport by clearing the override."""
        await self.session.page.send(
            cdp.emulation.set_device_metrics_override(
                width=0,
                height=0,
                device_scale_factor=0.0,
                mobile=False,
            )
        )
        return "Viewport override cleared"

    async def set_device(self, profile: str) -> str:
        """Emulate a preset device.

        Call ``list_devices`` to see available profile keys
        (e.g. ``iphone-15-pro``, ``pixel-8``, ``ipad-pro``, ``desktop-1080p``).
        Also sets a matching User-Agent when the profile provides one.
        """
        key = profile.lower()
        if key not in DEVICE_PROFILES:
            return f"Error: Unknown device '{profile}'. Try list_devices()."
        p = DEVICE_PROFILES[key]
        await self.session.page.send(
            cdp.emulation.set_device_metrics_override(
                width=p.width,
                height=p.height,
                device_scale_factor=p.device_scale_factor,
                mobile=p.mobile,
            )
        )
        if p.user_agent:
            await self.session.page.send(
                cdp.network.set_user_agent_override(user_agent=p.user_agent)
            )
        await self.session.page.send(cdp.emulation.set_touch_emulation_enabled(enabled=p.mobile))
        return f"Emulating {p.name}: {p.width}x{p.height}@{p.device_scale_factor}x"

    async def list_devices(self) -> list[str]:
        """Return the list of available device profile keys."""
        return sorted(DEVICE_PROFILES.keys())

    async def set_cpu_throttle(self, rate: float = 1.0) -> str:
        """Slow the CPU by a multiplier.

        ``1.0`` disables throttling, ``4.0`` is DevTools' "4x slowdown",
        ``20.0`` roughly emulates a low-end phone.
        """
        if rate < 1.0:
            return "Error: rate must be >= 1.0"
        await self.session.page.send(cdp.emulation.set_cpu_throttling_rate(rate=rate))
        return f"CPU throttle: {rate}x"

    async def set_network_conditions(self, profile: str) -> str:
        """Apply a named network-throttling preset.

        Options: ``offline``, ``slow-3g``, ``fast-3g``, ``4g``,
        ``no-throttling``. See ``list_network_profiles`` for the numeric
        values.
        """
        key = profile.lower()
        if key not in NETWORK_PROFILES:
            return f"Error: Unknown profile '{profile}'. Try list_network_profiles()."
        p = NETWORK_PROFILES[key]
        await self.session.page.send(
            cdp.network.emulate_network_conditions(
                offline=bool(p["offline"]),
                latency=p["latency"],
                download_throughput=p["download"],
                upload_throughput=p["upload"],
            )
        )
        return f"Network profile: {key}"

    async def list_network_profiles(self) -> dict[str, dict[str, float]]:
        """Return the mapping of profile name -> CDP parameters."""
        return NETWORK_PROFILES

    async def emulate_media(self, media: str = "", prefers_color_scheme: str = "") -> str:
        """Force a CSS media type and/or ``prefers-color-scheme``.

        ``media`` is typically ``""`` (default), ``"screen"``, or ``"print"``.
        ``prefers_color_scheme`` is ``""`` (default), ``"light"``, or ``"dark"``.
        """
        features = []
        if prefers_color_scheme:
            features.append(
                cdp.emulation.MediaFeature(name="prefers-color-scheme", value=prefers_color_scheme)
            )
        await self.session.page.send(
            cdp.emulation.set_emulated_media(media=media or None, features=features or None)
        )
        return f"Media: {media or 'default'}, prefers-color-scheme: {prefers_color_scheme or 'default'}"
