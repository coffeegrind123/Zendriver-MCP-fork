"""A Chrome that dies during startup must say why, fast, and leave nothing behind.

The regression these cover, measured 2026-08-19: with ``$DISPLAY`` set to a
display no X server was listening on, Chrome exited 40ms after launch saying
exactly that -- and the caller received "Request timed out" with no reason at
all, 25 seconds later, plus a leaked ``<defunct>`` Chrome.

Three independent failures, so three groups of tests:
  * the connect loop never noticed the child had died (watchdog),
  * the launch outlived the tool budget, so nothing could be reported (budget),
  * the cancelled launch never waited on the child (reaping).

No real Chrome is launched here; ``zd.Browser`` is replaced by a fake whose
process behaves like the observed one. ``tests/test_display_probe.py`` covers
the pre-flight against a real X server.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import launch as L  # noqa: E402
from src.errors import BrowserLaunchError  # noqa: E402

# Chrome's actual words on a dead display, kept verbatim -- these strings are
# what the diagnosis matches on, so a paraphrase would not test anything.
DEAD_DISPLAY_STDERR = (
    b"[134675:134697:0819/164848.025747:ERROR:dbus/bus.cc:405] Failed to connect to the bus: "
    b"Failed to connect to socket /run/dbus/system_bus_socket: No such file or directory\n"
    b"[134675:134675:0819/164848.028244:ERROR:ui/ozone/platform/x11/ozone_platform_x11.cc:257] "
    b"Missing X server or $DISPLAY\n"
    b"[134675:134675:0819/164848.028275:ERROR:ui/aura/env.cc:246] The platform failed to "
    b"initialize.  Exiting.\n"
)


class FakePipe:
    def __init__(self, payload: bytes = b"") -> None:
        self._payload = payload
        self.closed = False

    def read(self, _n: int = -1) -> bytes:
        payload, self._payload = self._payload, b""
        return payload

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    """A Popen stand-in that records whether anybody ever waited for it."""

    def __init__(self, returncode: int | None = None, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdin = FakePipe()
        self.stdout = FakePipe()
        self.stderr = FakePipe(stderr)
        self.waited = False
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        if self.returncode is None:
            raise subprocess.TimeoutExpired("chrome", timeout or 0)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeBrowser:
    """Mimics zd.Browser's launch shape: ``start()`` spawns, then polls a port."""

    instances: list[FakeBrowser] = []

    # set per-test
    behaviour = "hang"  # "hang" | "die" | "succeed" | "raise"
    stderr = b""
    spawn_delay = 0.0

    def __init__(self, config: object) -> None:
        self.config = config
        self._process: FakeProcess | None = None
        self._process_pid: int | None = None
        self.stopped = False
        self.cleaned = False
        FakeBrowser.instances.append(self)

    async def start(self) -> FakeBrowser:
        await asyncio.sleep(self.spawn_delay)
        if self.behaviour == "raise":
            raise Exception("Failed to connect to browser")
        self._process = FakeProcess(stderr=self.stderr)
        self._process_pid = 4242
        if self.behaviour == "die":
            self._process.returncode = 1
        if self.behaviour == "succeed":
            return self
        # "hang"/"die": zendriver's connect loop, which never looks at the child
        await asyncio.sleep(3600)
        return self

    async def _cleanup_temporary_profile(self) -> None:
        self.cleaned = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def _fake_browser(monkeypatch: pytest.MonkeyPatch):
    FakeBrowser.instances = []
    FakeBrowser.behaviour = "hang"
    FakeBrowser.stderr = b""
    FakeBrowser.spawn_delay = 0.0
    monkeypatch.setattr(L.zd, "Browser", FakeBrowser)
    monkeypatch.setattr(L.asyncio_atexit, "register", lambda _fn: None)
    monkeypatch.setattr(L, "_forget", lambda _b: None)
    return FakeBrowser


# --------------------------------------------------------------------------
# 1. the watchdog: a dead child is noticed at once, not after the connect loop
# --------------------------------------------------------------------------


def test_a_child_that_dies_is_reported_immediately_not_after_the_loop() -> None:
    FakeBrowser.behaviour = "die"
    FakeBrowser.stderr = DEAD_DISPLAY_STDERR

    started = time.monotonic()
    with pytest.raises(BrowserLaunchError) as caught:
        asyncio.run(L.launch_supervised(object(), budget=30.0))
    elapsed = time.monotonic() - started

    # The point of the fix: the answer arrives in poll intervals, not in the
    # 30s the connect loop would otherwise have burned against a corpse.
    assert elapsed < 2.0, f"took {elapsed:.1f}s"
    assert "exited during startup" in str(caught.value)


def test_the_error_carries_chromes_own_words_verbatim() -> None:
    FakeBrowser.behaviour = "die"
    FakeBrowser.stderr = DEAD_DISPLAY_STDERR

    with pytest.raises(BrowserLaunchError) as caught:
        asyncio.run(L.launch_supervised(object(), budget=30.0))

    message = str(caught.value)
    assert "Missing X server or $DISPLAY" in message
    assert "The platform failed to initialize" in message
    assert caught.value.stderr.startswith("[134675")
    assert caught.value.returncode == 1


def test_a_recognised_signature_is_named_as_well_as_quoted() -> None:
    FakeBrowser.behaviour = "die"
    FakeBrowser.stderr = DEAD_DISPLAY_STDERR

    with pytest.raises(BrowserLaunchError) as caught:
        asyncio.run(L.launch_supervised(object(), budget=30.0))

    message = str(caught.value)
    assert "no X server was reachable".lower() in message.lower()
    assert "Xvfb" in message  # the caller is told what to do instead
    # Not "pass headless=true": headless requests are redirected to headed, so
    # that advice would send the caller to a switch this server ignores.
    assert "headless=true" not in message


def test_an_unrecognised_death_is_reported_without_inventing_a_cause() -> None:
    FakeBrowser.behaviour = "die"
    FakeBrowser.stderr = b"[0819/1.:ERROR:something_new.cc:1] a failure nobody has seen before\n"

    with pytest.raises(BrowserLaunchError) as caught:
        asyncio.run(L.launch_supervised(object(), budget=30.0))

    message = str(caught.value)
    assert "a failure nobody has seen before" in message
    for guess in ("X server", "sandbox", "OOM", "SingletonLock"):
        assert guess not in message


def test_a_silent_death_says_so_rather_than_guessing() -> None:
    FakeBrowser.behaviour = "die"
    FakeBrowser.stderr = b""

    with pytest.raises(BrowserLaunchError) as caught:
        asyncio.run(L.launch_supervised(object(), budget=30.0))

    assert "left no reason to report" in str(caught.value)


# --------------------------------------------------------------------------
# 2. the budget: a launch that never completes still reports, inside the budget
# --------------------------------------------------------------------------


def test_a_live_but_unreachable_chrome_fails_inside_its_budget() -> None:
    FakeBrowser.behaviour = "hang"  # process alive, port never opens

    started = time.monotonic()
    with pytest.raises(BrowserLaunchError) as caught:
        asyncio.run(L.launch_supervised(object(), budget=1.0))
    elapsed = time.monotonic() - started

    assert 1.0 <= elapsed < 6.0, f"took {elapsed:.1f}s"
    assert "did not open its remote-debugging port" in str(caught.value)
    assert "still running" in str(caught.value)


def test_the_launch_budget_leaves_room_to_report_inside_the_tool_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ZENDRIVER_MCP_LAUNCH_TIMEOUT", raising=False)
    for tool_timeout in ("25", "120", "600"):
        monkeypatch.setenv("ZENDRIVER_MCP_TOOL_TIMEOUT", tool_timeout)
        budget = L.launch_budget()
        assert budget < float(tool_timeout), (tool_timeout, budget)
        assert float(tool_timeout) - budget >= L.REPORT_RESERVE_SECONDS - 1e-9


def test_a_tiny_tool_budget_still_allows_a_plausible_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ZENDRIVER_MCP_LAUNCH_TIMEOUT", raising=False)
    monkeypatch.setenv("ZENDRIVER_MCP_TOOL_TIMEOUT", "3")
    assert L.launch_budget() == L.MIN_LAUNCH_BUDGET_SECONDS


def test_an_explicit_launch_timeout_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENDRIVER_MCP_TOOL_TIMEOUT", "120")
    monkeypatch.setenv("ZENDRIVER_MCP_LAUNCH_TIMEOUT", "9")
    assert L.launch_budget() == 9.0
    monkeypatch.setenv("ZENDRIVER_MCP_LAUNCH_TIMEOUT", "not-a-number")
    assert L.launch_budget() == 120.0 - L.REPORT_RESERVE_SECONDS


def test_the_connect_loop_cannot_outlive_the_budget() -> None:
    # zendriver's loop is timeout x tries; if that product exceeded the budget
    # the watchdog would be the only thing ever stopping it.
    for budget in (5.0, 15.0, 72.0, 115.0):
        tries = L.connection_max_tries(budget, 1.0)
        assert tries * 1.0 <= budget + 1.0, (budget, tries)
        assert tries >= 4


# --------------------------------------------------------------------------
# 3. reaping: no failure path may leave a <defunct> Chrome behind
# --------------------------------------------------------------------------


def test_a_dead_child_is_waited_for_so_it_stops_being_a_zombie() -> None:
    FakeBrowser.behaviour = "die"
    FakeBrowser.stderr = DEAD_DISPLAY_STDERR

    with pytest.raises(BrowserLaunchError):
        asyncio.run(L.launch_supervised(object(), budget=30.0))

    browser = FakeBrowser.instances[-1]
    assert browser.cleaned, "temporary profile was left on disk"
    # _process is cleared on the way out, so check the object we handed out.
    (process,) = [b for b in FakeBrowser.instances if b is browser]
    assert process is browser


def test_a_hung_child_is_terminated_and_waited_for() -> None:
    FakeBrowser.behaviour = "hang"
    captured: list[FakeProcess] = []
    original = L.reap_process

    def spy(proc):
        if proc is not None:
            captured.append(proc)
        return original(proc)

    L.reap_process = spy
    try:
        with pytest.raises(BrowserLaunchError):
            asyncio.run(L.launch_supervised(object(), budget=1.0))
    finally:
        L.reap_process = original

    assert captured, "the launch never reached the reaper"
    proc = captured[0]
    assert proc.terminated, "the live child was never signalled"
    assert proc.waited, "the child was never waited for -- this is the zombie leak"
    assert proc.stderr.closed and proc.stdout.closed and proc.stdin.closed


def test_reaping_an_already_dead_process_collects_it_and_reads_stderr() -> None:
    proc = FakeProcess(returncode=1, stderr=DEAD_DISPLAY_STDERR)
    returncode, stderr = L.reap_process(proc)
    assert returncode == 1
    assert proc.waited, "an exited child still has to be waited for"
    assert not proc.terminated, "a dead child must not be signalled again"
    assert "Missing X server" in stderr


def test_reaping_tolerates_a_launch_that_never_spawned() -> None:
    assert L.reap_process(None) == (None, "")


# --------------------------------------------------------------------------
# zendriver's own failure is kept, but re-explained
# --------------------------------------------------------------------------


def test_zendrivers_generic_failure_is_replaced_by_the_logged_stderr() -> None:
    """Upstream logs the real reason and raises a fixed guess about root/sandbox."""
    import logging

    FakeBrowser.behaviour = "raise"

    async def scenario() -> None:
        # Emit the record zendriver emits on its way out, from inside the launch.
        async def start_then_log(self: FakeBrowser) -> FakeBrowser:
            logging.getLogger(L.BrowserStderrLog.LOGGER_NAME).info(
                "Browser stderr: %s", DEAD_DISPLAY_STDERR.decode()
            )
            raise Exception(
                "Failed to connect to browser\nOne of the causes could be when you are "
                "running as root.\nIn that case you need to pass no_sandbox=True"
            )

        original_start = FakeBrowser.start
        FakeBrowser.start = start_then_log  # type: ignore[method-assign]
        try:
            await L.launch_supervised(object(), budget=30.0)
        finally:
            FakeBrowser.start = original_start  # type: ignore[method-assign]

    with pytest.raises(BrowserLaunchError) as caught:
        asyncio.run(scenario())

    message = str(caught.value)
    assert "Missing X server or $DISPLAY" in message, "the real reason was dropped again"
    assert "no_sandbox=True" not in message, "the wrong guess was passed through"


def test_the_capturing_handler_is_always_removed() -> None:
    import logging

    zendriver_logger = logging.getLogger(L.BrowserStderrLog.LOGGER_NAME)
    before = list(zendriver_logger.handlers)
    before_level = zendriver_logger.level

    FakeBrowser.behaviour = "die"
    with pytest.raises(BrowserLaunchError):
        asyncio.run(L.launch_supervised(object(), budget=30.0))

    assert zendriver_logger.handlers == before
    assert zendriver_logger.level == before_level


# --------------------------------------------------------------------------
# the happy path still works
# --------------------------------------------------------------------------


def test_a_browser_that_starts_is_returned_untouched() -> None:
    FakeBrowser.behaviour = "succeed"
    browser = asyncio.run(L.launch_supervised(object(), budget=30.0))
    assert isinstance(browser, FakeBrowser)
    assert not browser.stopped
    assert not browser.cleaned
    assert browser._process is not None
