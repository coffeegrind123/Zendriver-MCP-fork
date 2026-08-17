"""navigate settles the page itself, and the settle counter does not saturate.

Both behaviours come from one observed failure. An agent navigated to a news
site and read it in the very next call:

    navigate(url=...)            -> "Navigated to https://www.bbc.com/news"
    get_text_content(max_chars=5000) -> "[chars 0-0 of 0]"

navigate's docstring promised it waited "for the page to load", so 0 characters
read as "this page has no text". Three round trips went into recovering from
that — a setTimeout that does not exist in the caller's sandbox, a tool search,
and finally an explicit wait_for_network — before the same read returned 8,029
characters. On a small context window those round trips are the whole budget.

The second bug is visible in the reply that finally worked:

    "Network idle after 0.6s (100 requests captured)"

Exactly 100, because the old loop compared len(get_network_logs(100)) against
its previous value and that list is capped at 100 entries. Once a page has made
100 requests the count can never change again, so the loop declares idle after
one idle_time regardless of what the network is doing. A busy page defeats the
wait precisely when the wait is needed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.session import BrowserSession
from src.tools.navigation import NavigationTools


class _FakePage:
    """Advances a scripted network log every time the waiter sleeps."""

    def __init__(
        self,
        session: _FakeSession,
        per_tick: int,
        ticks: int | None,
        ready_after: int | None = 0,
    ) -> None:
        # Polls of document.readyState before it reports "complete". 0 means the
        # document was ready immediately; None means it never becomes ready.
        # A large finite number will NOT stand in for that — the waiter spins far
        # faster than real time here and would burn through it inside the
        # timeout, which is the same trap the `ticks` script has.
        self._ready_after = ready_after
        self._ready_polls = 0
        self._session = session
        self._per_tick = per_tick
        # None means "never stops" — a page that polls or streams. A finite count
        # must not stand in for that: the waiter spins far faster than real time
        # here, so any finite script runs dry and the network looks settled.
        self._ticks = ticks
        self.waits = 0

    async def evaluate(self, script: str):
        if "readyState" in script:
            self._ready_polls += 1
            if self._ready_after is None:
                return "loading"
            return "complete" if self._ready_polls > self._ready_after else "loading"
        return None

    async def wait(self, _seconds: float) -> None:
        self.waits += 1
        if self._ticks is None:
            self._session._network_logs.extend([{"url": "x"}] * self._per_tick)
            return
        if self._ticks > 0:
            self._ticks -= 1
            self._session._network_logs.extend([{"url": "x"}] * self._per_tick)


class _FakeSession:
    """Only what wait_for_network_idle touches, plus a recorded navigate."""

    wait_for_network_idle = BrowserSession.wait_for_network_idle

    wait_for_document_ready = BrowserSession.wait_for_document_ready

    def __init__(
        self,
        per_tick: int = 0,
        ticks: int | None = 0,
        preloaded: int = 0,
        ready_after: int | None = 0,
    ) -> None:
        self._network_logs: list[dict[str, Any]] = [{"url": "seed"}] * preloaded
        self.page = _FakePage(self, per_tick, ticks, ready_after)
        self.navigated: list[str] = []

    async def navigate(self, url: str) -> None:
        self.navigated.append(url)


def _tools(session: Any) -> NavigationTools:
    tools = NavigationTools.__new__(NavigationTools)
    tools._session = session
    return tools


# NavigationTools reaches the session through a `session` property on ToolBase;
# bypass __init__ (which registers tools on the real MCP app) and point it here.
def _make(session: Any) -> NavigationTools:
    tools = _tools(session)
    type(tools).session = property(lambda self: self._session)  # type: ignore[assignment]
    return tools


def test_idle_is_detected_once_requests_stop() -> None:
    session = _FakeSession(per_tick=3, ticks=2)
    idle, _elapsed, count = asyncio.run(session.wait_for_network_idle(timeout=5, idle_time=0.0))
    assert idle is True
    # 2 ticks of 3 requests each, then quiet.
    assert count == 6


def test_a_busy_network_times_out_instead_of_reporting_idle() -> None:
    # Never stops making requests: the waiter must give up, not claim idle.
    session = _FakeSession(per_tick=1, ticks=None)
    idle, _elapsed, count = asyncio.run(session.wait_for_network_idle(timeout=0.35, idle_time=0.2))
    assert idle is False
    assert count > 0


def test_counter_does_not_saturate_past_the_log_limit() -> None:
    """The regression: 100 already-captured requests must not fake an idle network.

    The old loop counted get_network_logs(100), whose length pins at 100. With
    100 entries preloaded and traffic still arriving, it saw 100 == 100 and
    returned "Network idle ... (100 requests captured)" — the exact string the
    failing session produced.
    """
    session = _FakeSession(per_tick=5, ticks=None, preloaded=100)
    idle, _elapsed, count = asyncio.run(session.wait_for_network_idle(timeout=0.35, idle_time=0.2))
    assert idle is False, "a network still making requests must never report idle"
    assert count > 100, f"the count must keep rising past the log cap, got {count}"


def test_navigate_settles_and_says_so() -> None:
    session = _FakeSession(per_tick=2, ticks=1)
    out = asyncio.run(_make(session).navigate("https://example.com/news", settle=5))
    assert session.navigated == ["https://example.com/news"]
    assert "Navigated to https://example.com/news" in out
    assert "network idle after" in out
    assert "requests" in out


def test_navigate_reports_an_unsettled_page_rather_than_pretending() -> None:
    session = _FakeSession(per_tick=1, ticks=None)
    out = asyncio.run(_make(session).navigate("https://example.com/live", settle=0.3))
    assert "network still active after" in out


def test_settle_zero_skips_the_wait_entirely() -> None:
    session = _FakeSession(per_tick=1, ticks=None)
    out = asyncio.run(_make(session).navigate("https://example.com", settle=0))
    assert out == "Navigated to https://example.com (no settle requested)"
    assert session.page.waits == 0, "settle=0 must not sleep at all"


def test_navigate_no_longer_promises_a_wait_it_does_not_perform() -> None:
    doc = NavigationTools.navigate.__doc__ or ""
    assert "do NOT need a separate wait" in doc


# --- the regression: idle cannot tell "finished" from "not started" -----------


def test_document_readiness_is_awaited_before_the_idle_wait() -> None:
    """The bug this exists for.

    A real run returned `network idle after 0.5s, 322 requests` — 0.5s is exactly
    `idle_time`, so the very first sample already looked quiet and none of the
    navigation's own traffic was ever observed. The next `get_text_content`
    threw `TypeError: Cannot read properties of null (reading 'innerText')`,
    because `document.body` did not exist yet.

    A page that is not ready must not be reported as settled, however quiet the
    network happens to look.
    """
    session = _FakeSession(per_tick=0, ticks=0, ready_after=3)
    out = asyncio.run(_make(session).navigate("https://example.com/slow", settle=5))
    # The document became ready, so the settle is honest about having waited.
    assert "network idle after" in out
    assert session.page._ready_polls > 3, "readyState must actually have been polled"


def test_a_document_that_never_becomes_ready_is_reported_not_claimed_settled() -> None:
    session = _FakeSession(per_tick=0, ticks=0, ready_after=None)
    out = asyncio.run(_make(session).navigate("https://example.com/hang", settle=0.3))
    assert "document still loading" in out
    assert "network idle" not in out, "an unready document must never read as settled"


def test_wait_for_document_ready_reports_both_outcomes() -> None:
    ready, _ = asyncio.run(_FakeSession(ready_after=2).wait_for_document_ready(timeout=5))
    assert ready is True
    never, _ = asyncio.run(_FakeSession(ready_after=None).wait_for_document_ready(timeout=0.3))
    assert never is False
