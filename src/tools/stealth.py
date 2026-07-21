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

import asyncio
import logging

from zendriver import cdp
from zendriver.core.cloudflare import (
    cf_is_interactive_challenge_present,
    verify_cf,
)

from src.errors import CloudflareChallengeError
from src.tools.base import ToolBase

logger = logging.getLogger(__name__)


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

    async def _cf_cleared(self) -> bool:
        """True when neither an interactive challenge nor the "Just a moment" shell remains."""
        page = self.session.page
        try:
            if await cf_is_interactive_challenge_present(page, timeout=2):
                return False
        except Exception:
            pass  # a probe failure is not proof the challenge is gone; fall through to title
        try:
            title = (await page.evaluate("document.title") or "")
        except Exception:
            return False
        return "just a moment" not in title.lower()

    async def bypass_cloudflare(self, timeout: float = 30.0, click_delay: float = 4.0) -> str:
        """Solve a Cloudflare challenge (Turnstile or managed) on the current page.

        Resilient loop: zendriver's ``verify_cf`` captures Turnstile nodes up front, but the
        challenge iframe re-renders mid-solve, invalidating those node ids ("Node with given
        id does not belong to the document"). So we (1) poll for auto-clear — a headed stealth
        browser passes many managed challenges with NO click — and (2) retry ``verify_cf`` from
        scratch each round so every attempt re-finds FRESH elements, swallowing the stale-node
        error instead of aborting. Raises only if still challenged at ``timeout``.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last_err: Exception | None = None
        reloaded = False

        while loop.time() < deadline:
            if await self._cf_cleared():
                return "Cloudflare challenge solved"
            remaining = deadline - loop.time()
            try:
                # Fresh element discovery happens INSIDE verify_cf, so a stale node only
                # fails this attempt; the next round re-finds current nodes.
                await verify_cf(
                    self.session.page,
                    click_delay=click_delay,
                    timeout=max(4.0, min(12.0, remaining)),
                )
            except Exception as exc:  # TimeoutError, stale-node, transient CDP — all retryable
                last_err = exc
                logger.debug("verify_cf attempt failed (retrying): %s", exc)
                # A managed challenge with no clickable checkbox can still clear on its own;
                # one reload halfway through often nudges a wedged challenge past.
                if not reloaded and (deadline - loop.time()) > timeout / 2:
                    try:
                        await self.session.page.reload()
                        reloaded = True
                    except Exception:
                        pass
                await asyncio.sleep(1.0)

        if await self._cf_cleared():
            return "Cloudflare challenge solved"
        raise CloudflareChallengeError(
            f"Could not solve Cloudflare challenge within {timeout}s"
            + (f" (last error: {last_err})" if last_err else "")
        )

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
