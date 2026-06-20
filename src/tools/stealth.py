"""Stealth tools: Cloudflare (Turnstile) solver and identity overrides.

The Cloudflare tools wrap zendriver's BUILT-IN solver (zendriver.core.cloudflare),
which is already present — no new dependency. `bypass_cloudflare` finds the Turnstile
checkbox iframe (piercing shadow DOM), reads its box model, and clicks the checkbox via
CDP mouse events until the challenge clears.

NOTE: a HEADED browser is what actually beats Cloudflare for most sites (in --headless=new
the click is rejected); these tools are the belt-and-suspenders for sites that still throw
an interactive challenge even headed. (Ported from bituq/zendriver-mcp.)
"""

from __future__ import annotations

from zendriver import cdp
from zendriver.core.cloudflare import (
    cf_is_interactive_challenge_present,
    verify_cf,
)

from src.errors import CloudflareChallengeError
from src.tools.base import ToolBase


class StealthTools(ToolBase):
    """Tools for evading bot detection and overriding browser identity."""

    def _register_tools(self) -> None:
        # the solver clicks for up to `timeout` seconds; give the guard room
        self._register(self.bypass_cloudflare, timeout=180)
        self._register(self.is_cloudflare_challenge_present, timeout=30)
        self._register(self.set_user_agent)
        self._register(self.clear_user_agent)
        self._register(self.set_locale)
        self._register(self.set_timezone)
        self._register(self.set_geolocation)

    async def bypass_cloudflare(self, timeout: float = 20.0, click_delay: float = 4.0) -> str:
        """Solve a Cloudflare interactive (Turnstile) challenge on the current page.

        Waits up to ``timeout`` seconds for the challenge iframe, then clicks the checkbox
        every ``click_delay`` seconds until it clears. Raises if it cannot be solved in time.
        """
        try:
            await verify_cf(self.session.page, click_delay=click_delay, timeout=timeout)
        except TimeoutError as exc:
            raise CloudflareChallengeError(str(exc)) from exc
        return "Cloudflare challenge solved"

    async def is_cloudflare_challenge_present(self, timeout: float = 5.0) -> bool:
        """Report whether a Cloudflare interactive challenge is visible. Fast probe — use
        before ``bypass_cloudflare`` to skip the click cycle when the page already passed."""
        return await cf_is_interactive_challenge_present(self.session.page, timeout=timeout)

    async def set_user_agent(
        self, user_agent: str, accept_language: str | None = None, platform: str | None = None
    ) -> str:
        """Override User-Agent, Accept-Language, and navigator.platform on the current tab."""
        await self.session.page.send(
            cdp.network.set_user_agent_override(
                user_agent=user_agent, accept_language=accept_language, platform=platform
            )
        )
        return f"User-Agent overridden: {user_agent}"

    async def clear_user_agent(self) -> str:
        """Restore the browser's real User-Agent (Browser.getVersion returns the real UA
        regardless of overrides; setting empty would be MORE fingerprintable)."""
        connection = self.session.browser.connection
        if connection is None:
            return "Browser has no active connection"
        _, _, _, real_ua, _ = await connection.send(cdp.browser.get_version())
        await self.session.page.send(cdp.network.set_user_agent_override(user_agent=real_ua))
        return f"User-Agent restored to default: {real_ua[:80]}"

    async def set_locale(self, locale: str) -> str:
        """Override the browser locale (e.g. ``en_US``); empty string restores system default."""
        await self.session.page.send(cdp.emulation.set_locale_override(locale=locale or None))
        return f"Locale set to: {locale or '(system default)'}"

    async def set_timezone(self, timezone_id: str) -> str:
        """Override the IANA timezone (e.g. ``Europe/Helsinki``); empty restores default."""
        await self.session.page.send(cdp.emulation.set_timezone_override(timezone_id=timezone_id))
        return f"Timezone set to: {timezone_id or '(system default)'}"

    async def set_geolocation(self, latitude: float, longitude: float, accuracy: float = 100.0) -> str:
        """Override the browser's geolocation. Accuracy is in metres."""
        await self.session.page.send(
            cdp.emulation.set_geolocation_override(
                latitude=latitude, longitude=longitude, accuracy=accuracy
            )
        )
        return f"Geolocation set to: lat={latitude}, lon={longitude}"
