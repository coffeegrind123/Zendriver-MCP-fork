"""Headless is not a mode: every request for one is redirected to headed.

The failure this exists for: a Turnstile challenge that never clears. Chrome in
``--headless=new`` has the checkbox click rejected, so the solver loops, the
tool times out, and the symptom reads as "Cloudflare is unbeatable here" rather
than "the browser was launched wrong". The switch is one boolean on a tool call,
which means any script, recipe or model turn can undo every stealth measure in
this server by accident.

So the policy is enforced at the launch layer, not in a signature: the two
lifecycle tools, the two proxy tools and the autostart path all converge on
``BrowserSession.start``, and these tests pin every one of those doors shut.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.errors import BrowserLaunchError  # noqa: E402
from src.launch import (  # noqa: E402
    HEADED_ONLY_NOTE,
    preflight_display,
    resolve_headless,
)
from src.session import BrowserSession  # noqa: E402


# --------------------------------------------------------------------------
# the decision itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("requested", [True, False, None, 1, "yes"])
def test_every_request_resolves_to_headed(requested: object) -> None:
    assert resolve_headless(requested) is False  # type: ignore[arg-type]


def test_the_override_is_announced_rather_than_silent(capsys: pytest.CaptureFixture) -> None:
    resolve_headless(True)
    captured = capsys.readouterr()
    assert "headless was requested and ignored" in captured.err
    assert "Xvfb" in captured.err, "the note must name the fix for a host with no display"
    # Once. It was twice while the log record was a warning: logging's
    # lastResort handler writes to stderr as well, so an unconfigured server
    # printed the same sentence a second time and it read as two overrides.
    assert captured.err.count("headless was requested and ignored") == 1


def test_a_headed_request_says_nothing(capsys: pytest.CaptureFixture) -> None:
    resolve_headless(False)
    assert capsys.readouterr().err == ""


# --------------------------------------------------------------------------
# the choke point every caller reaches
# --------------------------------------------------------------------------


class _FakeBrowser:
    main_tab = None
    stopped = False


def _launch_headless(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> object:
    """Run ``BrowserSession.start`` against a fake launcher, return the zd.Config."""
    import src.session as S

    captured: dict[str, object] = {}

    async def fake_launch(config, budget):  # noqa: ANN001
        captured["config"] = config
        return _FakeBrowser()

    async def fake_load_extensions(self, ext_dirs):  # noqa: ANN001
        return None

    monkeypatch.setattr(S, "launch_supervised", fake_launch)
    monkeypatch.setattr(S.BrowserSession, "EXTENSIONS", ())
    monkeypatch.setattr(S.BrowserSession, "_load_extensions", fake_load_extensions)
    # The pre-flight has its own tests; here it must not depend on this host
    # having a display.
    monkeypatch.setattr(S, "preflight_display", lambda: None)

    # BrowserSession.__new__ hands back the process-wide singleton, so a
    # previous test's fake browser would make start() a no-op ("already
    # running") and the assertion below would pass on nothing.
    session = S.BrowserSession()
    monkeypatch.setattr(session, "_browser", None)
    asyncio.run(session.start(**kwargs))  # type: ignore[arg-type]
    monkeypatch.setattr(session, "_browser", None)
    return captured["config"]


def test_session_start_launches_headed_even_when_asked_for_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _launch_headless(monkeypatch, headless=True)
    assert config.headless is False, "the flag reached Chrome — the redirect is not applied"


def test_session_start_is_headed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _launch_headless(monkeypatch)
    assert config.headless is False


# --------------------------------------------------------------------------
# what the caller is told
# --------------------------------------------------------------------------


def _tools(monkeypatch: pytest.MonkeyPatch, module: str, cls: str):
    """Build a tool module against a session whose start() records and returns."""
    from mcp.server.fastmcp import FastMCP

    import src.tools.base as base

    calls: list[dict] = []

    async def fake_start(*args: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))
        return object()

    async def fake_stop() -> None:
        return None

    # Through monkeypatch, not by assignment: the session is a singleton, and a
    # stub left on it would follow every later test in this process.
    session = BrowserSession()
    monkeypatch.setattr(session, "start", fake_start)
    monkeypatch.setattr(session, "stop", fake_stop)
    monkeypatch.setattr(base.BrowserSession, "get_instance", classmethod(lambda cls: session))

    mod = __import__(module, fromlist=[cls])
    return getattr(mod, cls)(FastMCP("test")), calls


def test_start_browser_reports_headed_and_names_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools, calls = _tools(monkeypatch, "src.tools.browser", "BrowserTools")

    message = asyncio.run(tools.start_browser(headless=True))

    assert "headed mode" in message
    assert "headless mode" not in message, "reporting the requested mode is the whole defect"
    assert HEADED_ONLY_NOTE in message
    # The flag is still forwarded: the session is the enforcement point, and a
    # tool that quietly rewrote it would leave the other callers unprotected.
    assert calls[0]["headless"] is True


def test_start_browser_says_nothing_extra_when_headed_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools, _ = _tools(monkeypatch, "src.tools.browser", "BrowserTools")
    message = asyncio.run(tools.start_browser())
    assert message == "Browser started in headed mode"


def test_the_proxy_restarts_carry_the_same_note(monkeypatch: pytest.MonkeyPatch) -> None:
    tools, _ = _tools(monkeypatch, "src.tools.proxy", "ProxyTools")

    configured = asyncio.run(tools.configure_proxy("http://10.0.0.1:8080", headless=True))
    cleared = asyncio.run(tools.clear_proxy(headless=True))

    assert HEADED_ONLY_NOTE in configured
    assert HEADED_ONLY_NOTE in cleared
    assert HEADED_ONLY_NOTE not in asyncio.run(tools.clear_proxy())


def test_the_start_browser_schema_does_not_advertise_headless() -> None:
    """A model reads the schema, not this file. It has to say "ignored" there."""
    from mcp.server.fastmcp import FastMCP

    import src.tools.browser as browser_module

    mcp = FastMCP("test")
    browser_module.BrowserTools(mcp)
    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "start_browser")
    description = tool.inputSchema["properties"]["headless"]["description"]
    assert "IGNORED" in description
    assert "always launches headed" in description


# --------------------------------------------------------------------------
# the way out that no longer exists
# --------------------------------------------------------------------------


def test_no_failure_path_offers_headless_as_the_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telling a caller to pass a flag that is ignored is worse than telling it nothing."""
    from src.launch import diagnose

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("ZENDRIVER_MCP_SKIP_DISPLAY_CHECK", raising=False)
    monkeypatch.setenv("DISPLAY", ":4242")
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(BrowserLaunchError) as caught:
        preflight_display()
    messages = [str(caught.value), diagnose("Missing X server or $DISPLAY", 1) or ""]

    for message in messages:
        assert "Xvfb" in message, "the real fix must still be named"
        assert "headless=true" not in message
        assert "headless true" not in message
