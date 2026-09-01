"""$DISPLAY being set is not the same as an X server being there.

The container this stack runs in has ``/tmp/.X11-unix/X99`` sitting on disk
from an Xvfb that died weeks earlier, with ``DISPLAY=:99`` exported and nothing
listening. Every existence check -- the variable, the socket file -- says yes;
Chrome exits instantly saying "Missing X server or $DISPLAY". So the probe has
to actually connect, and these tests pin that distinction from both sides.
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.errors import BrowserLaunchError  # noqa: E402
from src.launch import preflight_display, x_display_reachable  # noqa: E402

X11_DIR = "/tmp/.X11-unix"

needs_x11_dir = pytest.mark.skipif(
    not os.path.isdir(X11_DIR) or not os.access(X11_DIR, os.W_OK),
    reason=f"{X11_DIR} is not writable here",
)


def _free_display() -> int:
    for number in range(90, 130):
        if not os.path.exists(f"{X11_DIR}/X{number}"):
            return number
    pytest.skip("no free display number")


@pytest.fixture
def listening_display():
    """A socket that accepts connections where an X server's would be."""
    number = _free_display()
    path = f"{X11_DIR}/X{number}"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    sock.listen(1)
    try:
        yield number
    finally:
        sock.close()
        if os.path.exists(path):
            os.unlink(path)


@pytest.fixture
def stale_display():
    """A socket FILE with nothing behind it -- the state that caused the outage."""
    number = _free_display()
    path = f"{X11_DIR}/X{number}"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    sock.close()  # bound, never listened, now gone: the file survives
    try:
        yield number
    finally:
        if os.path.exists(path):
            os.unlink(path)


@needs_x11_dir
def test_a_listening_display_is_reachable(listening_display: int) -> None:
    assert x_display_reachable(f":{listening_display}")
    assert x_display_reachable(f":{listening_display}.0"), "screen suffix must be tolerated"


@needs_x11_dir
def test_a_stale_socket_file_is_not_reachable(stale_display: int) -> None:
    path = f"{X11_DIR}/X{stale_display}"
    assert os.path.exists(path), "precondition: the file is there"
    assert not x_display_reachable(f":{stale_display}"), (
        "an existence check would pass here -- that is the bug"
    )


def test_an_unused_display_number_is_not_reachable() -> None:
    assert not x_display_reachable(":4242")


@pytest.mark.parametrize("display", ["", "99", ":", ":abc", "nonsense", ":-1"])
def test_malformed_display_values_are_not_reachable(display: str) -> None:
    assert not x_display_reachable(display)


def test_a_remote_display_that_refuses_connections_is_not_reachable() -> None:
    # port 6000 + 4242 is out of range, so this exercises the TCP branch's
    # failure path without depending on anything being closed on the host.
    assert not x_display_reachable("127.0.0.1:4242")


# --------------------------------------------------------------------------
# the pre-flight built on top of it
# --------------------------------------------------------------------------


@needs_x11_dir
def test_preflight_allows_a_headed_launch_when_a_display_answers(
    listening_display: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("ZENDRIVER_MCP_SKIP_DISPLAY_CHECK", raising=False)
    monkeypatch.setenv("DISPLAY", f":{listening_display}")
    preflight_display()  # must not raise


def test_preflight_refuses_a_headed_launch_into_a_dead_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("ZENDRIVER_MCP_SKIP_DISPLAY_CHECK", raising=False)
    monkeypatch.setenv("DISPLAY", ":4242")
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(BrowserLaunchError) as caught:
        preflight_display()

    message = str(caught.value)
    assert ":4242" in message, "the caller must be told which display was checked"
    assert "Xvfb" in message, "the way out has to be named"
    # There used to be a second way out — "or call start_browser with
    # headless=true". Headless is now redirected to headed (src/launch.py), so
    # offering it would send the caller to a flag that is ignored.
    assert "headless=true" not in message


def test_preflight_says_when_display_is_not_set_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("ZENDRIVER_MCP_SKIP_DISPLAY_CHECK", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(BrowserLaunchError) as caught:
        preflight_display()
    assert "$DISPLAY is not set" in str(caught.value)


def test_a_headless_request_cannot_skip_the_display_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old escape route, closed.

    ``preflight_display`` used to return early for a headless launch, which was
    correct while headless was a mode. It is not one any more — every launch is
    headed — so the check applies to every launch, and the function no longer
    takes a flag that could turn it off.
    """
    import inspect

    from src.launch import resolve_headless

    assert "headless" not in inspect.signature(preflight_display).parameters
    assert resolve_headless(True) is False

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("ZENDRIVER_MCP_SKIP_DISPLAY_CHECK", raising=False)
    monkeypatch.setenv("DISPLAY", ":4242")
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(BrowserLaunchError):
        preflight_display()


def test_preflight_stands_aside_under_wayland(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY", ":4242")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    preflight_display()


def test_preflight_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe must never be the only thing standing between a host and Chrome."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":4242")
    monkeypatch.setenv("ZENDRIVER_MCP_SKIP_DISPLAY_CHECK", "1")
    preflight_display()


def test_preflight_is_linux_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("ZENDRIVER_MCP_SKIP_DISPLAY_CHECK", raising=False)
    monkeypatch.setenv("DISPLAY", ":4242")
    monkeypatch.setattr(sys, "platform", "darwin")
    preflight_display()
