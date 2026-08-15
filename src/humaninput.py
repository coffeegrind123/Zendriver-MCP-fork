"""Human-like input primitives on top of CDP Input.dispatch* events.

The idea: real users don't teleport the cursor or type at constant WPM.
Bot-detection systems look at velocity curves and inter-keystroke timing.
We keep things simple (no heavy ML) but still much more natural than
`element.click()` or `insertText`.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass

from zendriver import cdp
from zendriver.core.tab import Tab

# Reasonable physical bounds.
_MIN_MOUSE_STEPS = 20
_MAX_MOUSE_STEPS = 120
_DEFAULT_MOUSE_DURATION = 0.4  # seconds


@dataclass(frozen=True)
class Point:
    x: float
    y: float


def _cubic_bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    """Standard cubic Bezier at parameter t in [0, 1]."""
    u = 1.0 - t
    x = (u**3) * p0.x + 3 * (u**2) * t * p1.x + 3 * u * (t**2) * p2.x + (t**3) * p3.x
    y = (u**3) * p0.y + 3 * (u**2) * t * p1.y + 3 * u * (t**2) * p2.y + (t**3) * p3.y
    return Point(x, y)


def bezier_path(
    start: Point,
    end: Point,
    steps: int,
    jitter: float = 0.08,
    rng: random.Random | None = None,
) -> list[Point]:
    """Generate a humanish mouse path from ``start`` to ``end``.

    Uses a cubic Bezier whose two control points are offset perpendicular to
    the straight line by up to ``jitter`` * distance. Adds per-sample jitter
    so the path is slightly noisy. Deterministic when ``rng`` is provided.
    """
    r = rng or random.Random()
    steps = max(_MIN_MOUSE_STEPS, min(steps, _MAX_MOUSE_STEPS))

    dx = end.x - start.x
    dy = end.y - start.y
    distance = math.hypot(dx, dy) or 1.0

    # Perpendicular unit vector.
    nx = -dy / distance
    ny = dx / distance

    offset1 = jitter * distance * (r.random() * 2 - 1)
    offset2 = jitter * distance * (r.random() * 2 - 1)
    ctrl1 = Point(start.x + dx * 0.33 + nx * offset1, start.y + dy * 0.33 + ny * offset1)
    ctrl2 = Point(start.x + dx * 0.66 + nx * offset2, start.y + dy * 0.66 + ny * offset2)

    points: list[Point] = []
    for i in range(steps + 1):
        t = i / steps
        # Small per-sample jitter (<1px typical).
        jx = r.gauss(0, 0.4)
        jy = r.gauss(0, 0.4)
        p = _cubic_bezier(start, ctrl1, ctrl2, end, t)
        points.append(Point(p.x + jx, p.y + jy))
    return points


_CURSOR_ATTR = "_zendriver_mcp_cursor"


def get_last_cursor(tab: Tab) -> Point:
    """Return the last cursor position recorded on ``tab``, or the origin."""
    return getattr(tab, _CURSOR_ATTR, Point(0, 0))


def set_last_cursor(tab: Tab, point: Point) -> None:
    setattr(tab, _CURSOR_ATTR, point)


async def move_mouse(
    tab: Tab,
    end: Point,
    start: Point | None = None,
    duration: float = _DEFAULT_MOUSE_DURATION,
    rng: random.Random | None = None,
) -> None:
    """Move the mouse along a humanish path, dispatching mousemove events.

    Starts from the last known cursor position cached on the ``tab`` (or
    the explicit ``start`` argument) - so consecutive ``human_click``s
    don't teleport the cursor back to (0,0) between calls.
    """
    if start is None:
        start = get_last_cursor(tab)
    steps = max(_MIN_MOUSE_STEPS, int(duration * 180))
    path = bezier_path(start, end, steps=steps, rng=rng)
    per_step = duration / len(path)
    for point in path:
        await tab.send(cdp.input_.dispatch_mouse_event(type_="mouseMoved", x=point.x, y=point.y))
        await asyncio.sleep(per_step)
    set_last_cursor(tab, end)


async def human_click(
    tab: Tab,
    target: Point,
    start: Point | None = None,
    move_duration: float = _DEFAULT_MOUSE_DURATION,
    press_duration: float = 0.08,
    rng: random.Random | None = None,
) -> None:
    """Move the cursor to ``target`` and click with realistic timing."""
    r = rng or random.Random()
    await move_mouse(tab, target, start=start, duration=move_duration, rng=r)

    await tab.send(
        cdp.input_.dispatch_mouse_event(
            type_="mousePressed",
            x=target.x,
            y=target.y,
            button=cdp.input_.MouseButton.LEFT,
            click_count=1,
        )
    )
    # Hold - a real click is ~60-120ms.
    await asyncio.sleep(press_duration + r.gauss(0, 0.02))
    await tab.send(
        cdp.input_.dispatch_mouse_event(
            type_="mouseReleased",
            x=target.x,
            y=target.y,
            button=cdp.input_.MouseButton.LEFT,
            click_count=1,
        )
    )


def keystroke_delays(
    length: int,
    wpm: int = 220,
    rng: random.Random | None = None,
) -> list[float]:
    """Generate per-keystroke delays modelling typing at ``wpm``.

    Assumes an average word length of 5 chars. Delays are gaussian around the
    target with a small floor so we never dispatch instant bursts.
    """
    r = rng or random.Random()
    mean = 60.0 / (wpm * 5)  # seconds per char
    stddev = mean * 0.35
    return [max(0.02, r.gauss(mean, stddev)) for _ in range(length)]


async def human_type(
    tab: Tab,
    text: str,
    wpm: int = 220,
    rng: random.Random | None = None,
) -> None:
    """Type ``text`` character-by-character with realistic delays.

    Uses ``Input.insertText`` per character so IME, contenteditable, and
    numeric inputs all behave correctly. For dead-key sensitive fields
    (terminals, games) use ``dispatch_key_event`` directly.
    """
    delays = keystroke_delays(len(text), wpm=wpm, rng=rng)
    for char, delay in zip(text, delays, strict=True):
        await tab.send(cdp.input_.insert_text(text=char))
        await asyncio.sleep(delay)
