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
from typing import Annotated

from pydantic import Field
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
            title = await page.evaluate("document.title") or ""
        except Exception:
            return False
        return "just a moment" not in title.lower()  # type: ignore[union-attr]

    async def bypass_cloudflare(
        self,
        timeout: Annotated[
            float,
            Field(description="Maximum seconds to keep retrying before raising. Example: 30.0"),
        ] = 30.0,
        click_delay: Annotated[
            float,
            Field(
                description="Seconds to pause before clicking the Turnstile checkbox, mimicking human hesitation. Too short reads as automation. Example: 4.0"
            ),
        ] = 4.0,
    ) -> str:
        """Solve a Cloudflare challenge (Turnstile or managed) on the current page.

        Call after landing on a page that shows "Just a moment"; check first with
        is_cloudflare_challenge_present to skip the click cycle when the page
        already passed. Requires a HEADED browser — in headless the click is
        rejected, so start_browser with headless false. Returns 'Cloudflare
        challenge solved', or raises CloudflareChallengeError if still challenged
        at timeout.

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

    async def is_cloudflare_challenge_present(
        self,
        timeout: Annotated[
            float,
            Field(
                description="Maximum seconds to look for the challenge before concluding it is absent. Example: 5.0"
            ),
        ] = 5.0,
    ) -> bool:
        """Check whether a Cloudflare interactive challenge is currently on screen.

        A fast probe to run before bypass_cloudflare, so the slow click cycle is
        skipped on pages that already passed. Returns true when an interactive
        challenge is present, false otherwise.
        """
        return await cf_is_interactive_challenge_present(self.session.page, timeout=timeout)

    async def set_user_agent(
        self,
        user_agent: Annotated[
            str,
            Field(
                description="Full User-Agent string to send. Example: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'"
            ),
        ],
        accept_language: Annotated[
            str | None,
            Field(
                description="Accept-Language header value. Omit to leave it unchanged. Example: 'en-GB,en;q=0.9'"
            ),
        ] = None,
        platform: Annotated[
            str | None,
            Field(
                description="Value reported by navigator.platform. Should agree with the user_agent or the mismatch is itself a detection signal. Example: 'Win32'"
            ),
        ] = None,
    ) -> str:
        """Override the User-Agent, Accept-Language, and navigator.platform for this tab.

        Applies to the current tab only, and does not survive creating a new one.
        Keep the three values mutually consistent — a Windows UA reporting
        platform 'Linux x86_64' is more detectable than no override at all. Use
        clear_user_agent to undo. Returns a confirmation naming the UA applied.
        """
        await self.session.page.send(
            cdp.network.set_user_agent_override(
                user_agent=user_agent, accept_language=accept_language, platform=platform
            )
        )
        return f"User-Agent overridden: {user_agent}"

    async def clear_user_agent(self) -> str:
        """Restore the browser's genuine User-Agent after set_user_agent.

        Reads the real UA back from the browser and re-applies it, because
        clearing the override to an empty string would itself be a fingerprinting
        signal. Returns the restored User-Agent, truncated to 80 characters, or a
        notice if the browser has no active connection.
        """
        connection = self.session.browser.connection
        if connection is None:
            return "Browser has no active connection"
        _, _, _, real_ua, _ = await connection.send(cdp.browser.get_version())
        await self.session.page.send(cdp.network.set_user_agent_override(user_agent=real_ua))
        return f"User-Agent restored to default: {real_ua[:80]}"

    async def set_locale(
        self,
        locale: Annotated[
            str,
            Field(
                description="Locale identifier such as 'en_US' or 'fi_FI'. Pass an empty string to restore the system default. Example: 'en_US'"
            ),
        ],
    ) -> str:
        """Override the browser's reported locale, affecting language and formatting.

        Changes what the page sees from Intl and navigator.language. Keep it
        consistent with set_timezone and the Accept-Language passed to
        set_user_agent — a mismatched trio is a detection signal. Returns a
        confirmation naming the locale applied.
        """
        await self.session.page.send(cdp.emulation.set_locale_override(locale=locale or None))
        return f"Locale set to: {locale or '(system default)'}"

    async def set_timezone(
        self,
        timezone_id: Annotated[
            str,
            Field(
                description="IANA timezone identifier, not a UTC offset. Pass an empty string to restore the system default. Example: 'Europe/Helsinki'"
            ),
        ],
    ) -> str:
        """Override the browser's reported timezone.

        Changes what Date and Intl report to the page. Keep it consistent with
        set_locale and set_geolocation — a Helsinki locale on a New York clock is
        a detection signal. Returns a confirmation naming the timezone applied.
        """
        await self.session.page.send(cdp.emulation.set_timezone_override(timezone_id=timezone_id))
        return f"Timezone set to: {timezone_id or '(system default)'}"

    async def set_geolocation(
        self,
        latitude: Annotated[
            float,
            Field(description="Latitude in decimal degrees, positive north. Example: 60.1699"),
        ],
        longitude: Annotated[
            float,
            Field(description="Longitude in decimal degrees, positive east. Example: 24.9384"),
        ],
        accuracy: Annotated[
            float,
            Field(
                description="Reported accuracy radius in metres. Implausibly small values look synthetic. Example: 100.0"
            ),
        ] = 100.0,
    ) -> str:
        """Override the position returned by the browser's Geolocation API.

        Use for pages that gate content by location. The page must still be
        granted geolocation permission to ask. Keep it consistent with
        set_timezone and set_locale. Returns a confirmation naming the
        coordinates applied.
        """
        await self.session.page.send(
            cdp.emulation.set_geolocation_override(
                latitude=latitude, longitude=longitude, accuracy=accuracy
            )
        )
        return f"Geolocation set to: lat={latitude}, lon={longitude}"
