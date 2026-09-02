"""A Chrome that HANGS is not a Chrome that has exited, and only one was detected.

Measured 2026-09-02 in the pi container. Chrome's GPU process crashed;
`chrome_crashpad_handler` ptrace-attached to snapshot it and never finished, so
the GPU process sat in `State: t (tracing stop)` with `TracerPid` pointing at the
handler, while the handler AND the browser's main thread both blocked in
`wchan: anon_pipe_write`. The browser's main thread is what serves DevTools, so
port 60907 accepted every TCP connection and sent zero bytes:

    /json/version -> timed out after 22000 ms with 0 bytes received
    /json/list    -> timed out after 35000 ms with 0 bytes received

one caller waited 1859 s. Throughout, `browser.stopped` was False -- the process
was alive -- so `is_dead()` said healthy, the recovery path in `_register` never
ran, and every tool call returned the same "exceeded its 25s time budget" for 22
hours.

The discriminator is the endpoint, not the process: a refused connection and a
silent one are both "gone"; a status line is "alive".
"""

from __future__ import annotations

import asyncio
import contextlib

from src.errors import BrowserUnreachableError
from src.session import BrowserSession, _cdp_answers


class _FakeBrowser:
    def __init__(self, url: str | None, stopped: bool = False) -> None:
        self.websocket_url = url
        self.stopped = stopped


@contextlib.asynccontextmanager
async def _server(handler):
    srv = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    async with srv:
        yield port


async def _answering(reader, writer):
    await reader.readline()
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    await writer.drain()
    writer.close()


async def _silent(reader, writer):
    # Exactly the wedge: the connection is ACCEPTED and nothing is ever sent.
    await asyncio.sleep(60)


async def _test_a_live_endpoint_answers() -> None:
    async with _server(_answering) as port:
        assert await _cdp_answers("127.0.0.1", port) is True


async def _test_a_silent_endpoint_is_unreachable_not_merely_slow() -> None:
    session = BrowserSession()
    async with _server(_silent) as port:
        session._browser = _FakeBrowser(f"ws://127.0.0.1:{port}/devtools/browser/x")
        # The probe must not inherit the caller's patience: this call has already
        # spent its whole budget, which is why it is asking.
        assert await session.is_unreachable(timeout=0.5) is True


async def _test_a_refused_port_is_unreachable_too() -> None:
    session = BrowserSession()
    async with _server(_answering) as port:
        pass  # closed on exit -- nothing is listening now
    session._browser = _FakeBrowser(f"ws://127.0.0.1:{port}/devtools/browser/x")
    assert await session.is_unreachable(timeout=1.0) is True


async def _test_a_healthy_browser_is_not_discarded() -> None:
    session = BrowserSession()
    async with _server(_answering) as port:
        session._browser = _FakeBrowser(f"ws://127.0.0.1:{port}/devtools/browser/x")
        assert await session.is_unreachable(timeout=2.0) is False
        assert await session.discard_if_unreachable(timeout=2.0) is False
        assert session._browser is not None  # still ours


async def _test_a_stopped_browser_is_unreachable_without_probing() -> None:
    session = BrowserSession()
    session._browser = _FakeBrowser("ws://127.0.0.1:1/devtools/browser/x", stopped=True)
    assert await session.is_unreachable(timeout=0.01) is True


async def _test_no_browser_is_not_unreachable() -> None:
    # "nothing to talk to" is BrowserNotStartedError's job, not this one's.
    session = BrowserSession()
    session._browser = None
    assert await session.is_unreachable(timeout=0.01) is False


async def _test_an_unparseable_url_does_not_claim_the_browser_is_broken() -> None:
    # Discarding a healthy session costs a relaunch and a lost tab, so an
    # unreadable address must fail toward "leave it alone".
    session = BrowserSession()
    session._browser = _FakeBrowser("not-a-url")
    assert await session.is_unreachable(timeout=0.01) is False
    session._browser = _FakeBrowser(None)
    assert await session.is_unreachable(timeout=0.01) is False


async def _test_discard_clears_the_session_so_the_next_call_starts_clean() -> None:
    session = BrowserSession()
    async with _server(_silent) as port:
        session._browser = _FakeBrowser(f"ws://127.0.0.1:{port}/devtools/browser/x")
        session._page = object()  # type: ignore[assignment]
        assert await session.discard_if_unreachable(timeout=0.5) is True
        assert session._browser is None
        assert session._page is None


def test_the_error_names_the_wedge_rather_than_the_clock() -> None:
    err = BrowserUnreachableError("navigate", 25.0, will_autostart=True)
    text = str(err)
    assert "stopped answering" in text
    assert "wedged" in text
    assert "The next call starts a fresh one." in text
    # And it is NOT the message that said nothing useful for 22 hours.
    assert "exceeded its" not in text

    manual = str(BrowserUnreachableError("navigate", 25.0, will_autostart=False))
    assert "Call start_browser" in manual


# --- pytest entry points (no pytest-asyncio in this repo) ---

def test_a_live_endpoint_answers() -> None:
    asyncio.run(_test_a_live_endpoint_answers())

def test_a_silent_endpoint_is_unreachable_not_merely_slow() -> None:
    asyncio.run(_test_a_silent_endpoint_is_unreachable_not_merely_slow())

def test_a_refused_port_is_unreachable_too() -> None:
    asyncio.run(_test_a_refused_port_is_unreachable_too())

def test_a_healthy_browser_is_not_discarded() -> None:
    asyncio.run(_test_a_healthy_browser_is_not_discarded())

def test_a_stopped_browser_is_unreachable_without_probing() -> None:
    asyncio.run(_test_a_stopped_browser_is_unreachable_without_probing())

def test_no_browser_is_not_unreachable() -> None:
    asyncio.run(_test_no_browser_is_not_unreachable())

def test_an_unparseable_url_does_not_claim_the_browser_is_broken() -> None:
    asyncio.run(_test_an_unparseable_url_does_not_claim_the_browser_is_broken())

def test_discard_clears_the_session_so_the_next_call_starts_clean() -> None:
    asyncio.run(_test_discard_clears_the_session_so_the_next_call_starts_clean())
