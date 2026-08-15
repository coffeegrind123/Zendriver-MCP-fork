"""Unit tests for humaninput primitives (no browser required)."""

from __future__ import annotations

import math
import random
import statistics

from src.humaninput import Point, bezier_path, keystroke_delays


def test_bezier_path_endpoints_match() -> None:
    start = Point(0, 0)
    end = Point(500, 300)
    path = bezier_path(start, end, steps=60, rng=random.Random(42))

    # Start and end should be at (or very near) the requested endpoints.
    # Per-sample jitter is tiny (gauss 0, 0.4).
    assert math.hypot(path[0].x - start.x, path[0].y - start.y) < 3
    assert math.hypot(path[-1].x - end.x, path[-1].y - end.y) < 3


def test_bezier_path_length_plausible() -> None:
    start = Point(0, 0)
    end = Point(100, 0)
    path = bezier_path(start, end, steps=60, rng=random.Random(1))

    total = sum(
        math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y)
        for i in range(len(path) - 1)
    )
    # Path follows a curve, so it's longer than the straight line, but not
    # absurdly so.
    straight = math.hypot(end.x - start.x, end.y - start.y)
    assert straight <= total <= straight * 2.5


def test_bezier_path_is_deterministic_with_seeded_rng() -> None:
    p1 = bezier_path(Point(0, 0), Point(100, 100), steps=30, rng=random.Random(99))
    p2 = bezier_path(Point(0, 0), Point(100, 100), steps=30, rng=random.Random(99))
    assert p1 == p2


def test_bezier_path_clamps_step_count() -> None:
    # Asking for absurd step counts should clamp, not crash.
    below = bezier_path(Point(0, 0), Point(10, 10), steps=1, rng=random.Random(0))
    above = bezier_path(Point(0, 0), Point(10, 10), steps=10_000, rng=random.Random(0))
    assert len(below) >= 20
    assert len(above) <= 200


def test_keystroke_delays_respect_wpm() -> None:
    delays = keystroke_delays(1000, wpm=220, rng=random.Random(7))
    # Average seconds per char at 220 wpm * 5 chars/word ~= 0.055s.
    avg = statistics.fmean(delays)
    assert 0.04 <= avg <= 0.07
    # No delay should be below the 20ms floor.
    assert min(delays) >= 0.02


def test_keystroke_delays_scale_inversely_with_wpm() -> None:
    slow = statistics.fmean(keystroke_delays(500, wpm=60, rng=random.Random(3)))
    fast = statistics.fmean(keystroke_delays(500, wpm=300, rng=random.Random(3)))
    assert slow > fast * 2
