# Supervised Chrome launch: notice death, say why, leave nothing behind.
#
# What this exists to fix, measured on 2026-08-19 against a $DISPLAY that was
# set to :99 with no X server behind it:
#
#   16:25:37.435  Chrome starts and exits ~40ms later, saying exactly what is
#                 wrong -- "Missing X server or $DISPLAY" / "The platform
#                 failed to initialize.  Exiting."
#   16:26:18      zendriver's connect loop (browser_connection_timeout x
#                 browser_connection_max_tries = 41s here) finally gives up,
#                 reads that stderr, and logs it. The loop never checks
#                 whether the child is still alive, so all 41 seconds were
#                 spent polling a corpse.
#   ...but the caller was already gone at 16:26:02, because 41s overshoots the
#                 25s tool budget: asyncio.wait_for had cancelled the tool.
#                 The client's whole error was "Request timed out".
#
# Reproduced both halves. A launch against a dead display raises zendriver's
# generic "Failed to connect to browser ... could be when you are running as
# root ... pass no_sandbox=True" -- a guess, and the wrong one, since this
# server already passes sandbox=False. And a launch cancelled mid-connect
# leaves the Popen unwaited, so the dead Chrome stays <defunct>: one leaked
# zombie per failed launch (confirmed, 1 -> 2 zombies from one cancellation).
#
# Three defects, three fixes, all of them needed:
#   1. watch the child process, so its death is noticed the moment it happens
#      instead of after a fixed-length connect loop;
#   2. keep the launch budget inside the tool budget, so the diagnosis is
#      actually delivered rather than cancelled a few seconds before it exists;
#   3. always terminate and *wait* for the child, so nothing is left defunct.
#
# Plus the reporting rule this stack cares about: hand the caller Chrome's own
# stderr verbatim, and add an interpretation only when a signature is
# recognised. An interpretation is the part most likely to be wrong; the raw
# bytes are what let a reader spot that.

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import subprocess
import sys
import time
from typing import Any

import asyncio_atexit
import zendriver as zd
from zendriver.core import util as zd_util

from src.errors import BrowserLaunchError

logger = logging.getLogger(__name__)

# How often the watchdog looks at the child while the connect loop runs.
LAUNCH_POLL_SECONDS = 0.1

# Seconds to let Chrome exit on SIGTERM before SIGKILL, per signal.
REAP_GRACE_SECONDS = 2.0

# Slice of the tool budget kept back for the failure path itself: terminate the
# child, wait for it (twice, worst case), read its stderr and format the error.
# A launch that spends the whole tool budget cannot report anything at all --
# asyncio.wait_for cancels it a moment before the diagnosis exists. Reserving a
# fixed cost rather than a proportion of the budget is deliberate: the cost IS
# fixed, and a proportion would squeeze a slow-but-working cold start on hosts
# with a short tool timeout.
REPORT_RESERVE_SECONDS = 2 * REAP_GRACE_SECONDS + 1.0

# Below this, no launch is plausible and the reserve has eaten everything.
MIN_LAUNCH_BUDGET_SECONDS = 5.0

# Chrome's startup errors are a handful of lines; this is only a sanity bound.
STDERR_READ_LIMIT = 1 << 16
STDERR_TAIL_LINES = 12
STDERR_TAIL_CHARS = 2000

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def tool_timeout_budget() -> float:
    """Seconds one MCP tool call may run, from ``$ZENDRIVER_MCP_TOOL_TIMEOUT``.

    Lives here rather than in ``src.tools.base`` so the launch path can respect
    the same number without importing the tool layer (which imports this one).
    """
    raw = os.environ.get("ZENDRIVER_MCP_TOOL_TIMEOUT", "120")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 120.0


def launch_budget() -> float:
    """Seconds a Chrome launch may take before it is declared failed.

    The tool budget minus the cost of reporting a failure, rather than a number
    of its own: the whole point of the supervision below is to produce a
    sentence naming the real cause, and a launch that runs to the end of the
    tool budget gets cancelled before that sentence exists.

    Override with ``$ZENDRIVER_MCP_LAUNCH_TIMEOUT`` on hosts where a cold Chrome
    genuinely needs longer -- and raise the tool timeout with it, or the
    diagnosis goes back to being unreachable. For scale: a warm launch on this
    stack's container host measures ~1s, a cold one a few seconds.
    """
    raw = os.environ.get("ZENDRIVER_MCP_LAUNCH_TIMEOUT", "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            logger.warning("ignoring unparseable ZENDRIVER_MCP_LAUNCH_TIMEOUT=%r", raw)
    return max(MIN_LAUNCH_BUDGET_SECONDS, tool_timeout_budget() - REPORT_RESERVE_SECONDS)


def connection_max_tries(budget: float, connection_timeout: float) -> int:
    """How many connect attempts fit in the budget.

    Container Chrome can take several seconds to open its CDP port, so the
    count wants to be generous -- but never so generous that zendriver's own
    loop outlives the budget and the watchdog is the only thing that ever
    stops it.
    """
    return max(4, int(budget / max(connection_timeout, 0.01)))


# --------------------------------------------------------------------------
# Pre-flight: is there a display to launch into at all?
# --------------------------------------------------------------------------


def x_display_reachable(display: str) -> bool:
    """Actually connect to ``$DISPLAY`` instead of trusting that it is set.

    A set-but-dead DISPLAY is the most common way Chrome dies instantly on a
    container host: the variable looks configured, Xvfb is not running, and
    Chrome exits before it has opened anything. The probe costs microseconds
    and is the difference between an immediate sentence and a 25-second
    silence.
    """
    if ":" not in display:
        return False
    host, _, tail = display.rpartition(":")
    screen = tail.split(".")[0]
    if not screen.isdigit():
        return False
    number = int(screen)

    if host and host != "unix":
        try:
            with socket.create_connection((host, 6000 + number), timeout=1.0):
                return True
        except OSError:
            return False

    path = f"/tmp/.X11-unix/X{number}"
    for address in (path, "\0" + path):  # filesystem socket, then Linux abstract
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(1.0)
            sock.connect(address)
            return True
        except OSError:
            continue
        finally:
            sock.close()
    return False


def preflight_display(headless: bool) -> None:
    """Refuse a headed launch when there is nothing to launch into.

    Raises ``BrowserLaunchError`` rather than letting Chrome discover it: the
    supervised launch below would catch that too, but seconds later and after
    spawning a process, and this is the case that actually happens.
    """
    if headless or sys.platform != "linux":
        return
    if _flag("ZENDRIVER_MCP_SKIP_DISPLAY_CHECK"):
        return
    if os.environ.get("WAYLAND_DISPLAY"):
        return  # not X11; Chrome has a display, just not one we can probe

    display = os.environ.get("DISPLAY", "").strip()
    if display and x_display_reachable(display):
        return

    if display:
        where = f"$DISPLAY={display!r} is set but nothing is listening on it"
    else:
        where = "$DISPLAY is not set"
    suggested = display or ":99"
    raise BrowserLaunchError(
        f"Chrome cannot start headed: no X server is reachable ({where}). "
        f"Start one -- e.g. `Xvfb {suggested} -screen 0 1920x1080x24 &` with "
        f"DISPLAY={suggested} -- or call start_browser with headless=true. "
        "Set ZENDRIVER_MCP_SKIP_DISPLAY_CHECK=1 to attempt the launch anyway."
    )


# --------------------------------------------------------------------------
# Reaping and reporting
# --------------------------------------------------------------------------


def reap_process(proc: subprocess.Popen[bytes] | None) -> tuple[int | None, str]:
    """Kill Chrome, *wait* for it, and return ``(returncode, stderr)``.

    The wait is the load-bearing part. zendriver only reaps inside
    ``Browser.stop()``, which never runs when a launch is cancelled, so the
    dead Chrome stays ``<defunct>`` for the lifetime of the server -- one
    leaked zombie per failed launch.

    Reading stderr only once the process is gone is deliberate too: the pipe is
    then at EOF, so the read returns immediately. Reading it while Chrome is
    still alive parks a thread on a blocking read that may never return, which
    is how the upstream helper leaks a thread per timeout.
    """
    if proc is None:
        return None, ""

    if proc.poll() is None:
        with contextlib.suppress(OSError):
            proc.terminate()
        try:
            proc.wait(timeout=REAP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=REAP_GRACE_SECONDS)
    else:
        proc.wait()  # already dead: collect it so it stops being a zombie

    stderr = ""
    if proc.stderr is not None:
        with contextlib.suppress(Exception):
            stderr = proc.stderr.read(STDERR_READ_LIMIT).decode("utf-8", "replace")
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.close()
    return proc.returncode, stderr


def diagnose(stderr: str, returncode: int | None) -> str | None:
    """Name the cause when Chrome's stderr carries a signature we recognise.

    Returns ``None`` rather than guessing. Every branch here was chosen because
    it is both unambiguous in Chrome's output and actionable by the caller;
    anything else is better served by the verbatim stderr.
    """
    low = stderr.lower()
    if (
        "missing x server" in low
        or "cannot open display" in low
        or "ozone_platform_x11" in low
        or "unable to open x display" in low
    ):
        display = os.environ.get("DISPLAY", "")
        where = f"$DISPLAY={display!r}" if display else "$DISPLAY (unset)"
        return (
            f"no X server was reachable at {where} -- start one (e.g. "
            f"`Xvfb {display or ':99'} -screen 0 1920x1080x24 &`) or call "
            "start_browser with headless=true"
        )
    if "singletonlock" in low or "processsingleton" in low or "profile appears to be in use" in low:
        return (
            "the profile directory is already locked by another Chrome -- usually a stale "
            "SingletonLock left by a crashed run; delete it or pass a different user_data_dir"
        )
    if "no usable sandbox" in low or "suid sandbox" in low:
        return (
            "Chrome's sandbox is unavailable on this host -- this server already launches with "
            "--no-sandbox, so a seccomp or user-namespace policy is blocking it"
        )
    if "/dev/shm" in low or "shared memory" in low:
        return (
            "/dev/shm is too small for Chrome's renderer -- call start_browser with low_memory=true"
        )
    if "error while loading shared libraries" in low or "not found" in low and "libx" in low:
        return "the Chrome build is missing a shared library it needs -- see the loader error below"
    if returncode == -9:
        return (
            "Chrome was killed with SIGKILL, which on a container host is almost always the "
            "OOM killer -- free memory or call start_browser with low_memory=true"
        )
    return None


def stderr_tail(stderr: str) -> str:
    """The last few meaningful lines of Chrome's stderr, bounded."""
    lines = [line.rstrip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""
    tail = "\n".join(lines[-STDERR_TAIL_LINES:])
    if len(tail) > STDERR_TAIL_CHARS:
        tail = "..." + tail[-STDERR_TAIL_CHARS:]
    return tail


def launch_message(headline: str, returncode: int | None, stderr: str) -> str:
    """Compose the caller-facing failure: what happened, why, and the evidence."""
    text = f"Chrome {headline}"
    if returncode is not None:
        text += f" (exit code {returncode})"
    text += "."

    reason = diagnose(stderr, returncode)
    if reason:
        text += " " + reason[0].upper() + reason[1:] + "."

    tail = stderr_tail(stderr)
    if tail:
        text += "\n\nChrome said:\n" + tail
    elif not reason:
        text += " Chrome wrote nothing to stderr, so it left no reason to report."
    return text


class BrowserStderrLog(logging.Handler):
    """Recover the one line zendriver logs and then throws away.

    When its connect loop times out, zendriver reads Chrome's stderr and sends
    it to ``logger.info("Browser stderr: %s", ...)`` -- then raises an
    exception that repeats none of it and guesses at "running as root"
    instead. The pipe is drained by that point, so that log record is the only
    surviving copy of the real reason. This lifts it back out.

    Only used as a fallback: when the watchdog sees the child die it reads the
    pipe itself, before zendriver gets there.
    """

    LOGGER_NAME = "zendriver.core.browser"
    PREFIX = "Browser stderr:"

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.text = ""
        self._logger = logging.getLogger(self.LOGGER_NAME)
        self._restore_level: int | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # a broken record must not break a launch
            return
        if message.startswith(self.PREFIX):
            captured = message[len(self.PREFIX) :].strip()
            if captured and captured != "No output from browser":
                self.text = captured

    def __enter__(self) -> BrowserStderrLog:
        # The record has to be emitted at all before a handler can see it, and
        # the default configuration leaves this logger below INFO.
        if not self._logger.isEnabledFor(logging.INFO):
            self._restore_level = self._logger.level
            self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._logger.removeHandler(self)
        if self._restore_level is not None:
            self._logger.setLevel(self._restore_level)
            self._restore_level = None


# --------------------------------------------------------------------------
# The supervised launch itself
# --------------------------------------------------------------------------


def _forget(browser: zd.Browser) -> None:
    """Drop a failed browser from zendriver's global instance registry.

    ``Browser.start`` adds itself there the moment the process is spawned and
    nothing ever removes it, so a failed launch would otherwise leave a dead
    object behind for the interpreter-exit sweep to try to stop.
    """
    with contextlib.suppress(Exception):
        zd_util.get_registered_instances().discard(browser)


def _adopt(browser: zd.Browser) -> None:
    """Attach the shutdown hook ``Browser.create`` would have attached.

    We cannot use ``Browser.create``/``zd.start`` here: both construct the
    ``Browser`` internally and only hand it back once the connect loop has
    finished, which is precisely too late to watch the child process. So the
    object is constructed here and this restores the one piece of ``create``
    that matters afterwards.
    """

    async def browser_atexit() -> None:
        if not browser.stopped:
            await browser.stop()
        await browser._cleanup_temporary_profile()

    asyncio_atexit.register(browser_atexit)


async def launch_supervised(config: zd.Config, budget: float) -> zd.Browser:
    """Start Chrome under a watchdog, or raise ``BrowserLaunchError`` saying why not.

    Returns a started ``zd.Browser``. On every failure path the child process
    is terminated and waited for, its temporary profile is cleaned up, and the
    error carries Chrome's own stderr.
    """
    browser = zd.Browser(config)
    task = asyncio.create_task(browser.start())
    deadline = time.monotonic() + budget
    started = time.monotonic()

    with BrowserStderrLog() as captured:
        while True:
            await asyncio.wait({task}, timeout=LAUNCH_POLL_SECONDS)

            if task.done():
                error = task.exception()
                if error is None:
                    _adopt(browser)
                    return browser
                # zendriver gave up on its own terms. Its message is a fixed
                # guess, so keep only the fact and supply the real reason from
                # the stderr it logged on the way out.
                logger.debug("zendriver launch failed: %r", error)
                headline = "failed to start"
                break

            process = browser._process
            if process is not None and process.poll() is not None:
                # The whole point: upstream keeps polling a CDP port for the
                # rest of the connect loop without ever looking at the child.
                headline = "exited during startup"
                break

            if time.monotonic() >= deadline:
                headline = (
                    f"did not open its remote-debugging port within {budget:.0f}s "
                    "(the process is still running but never became reachable)"
                )
                break

    task.cancel()
    with contextlib.suppress(BaseException):
        await task

    returncode, stderr = await asyncio.to_thread(reap_process, browser._process)
    if not stderr:
        stderr = captured.text
    browser._process = None
    browser._process_pid = None
    with contextlib.suppress(Exception):
        await browser._cleanup_temporary_profile()
    _forget(browser)

    logger.info(
        "chrome launch failed after %.1fs: %s (rc=%s)",
        time.monotonic() - started,
        headline,
        returncode,
    )
    raise BrowserLaunchError(
        launch_message(headline, returncode, stderr),
        returncode=returncode,
        stderr=stderr,
    )
