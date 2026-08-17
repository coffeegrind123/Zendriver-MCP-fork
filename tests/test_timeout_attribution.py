""""The tool was too slow" and "the thing it looked for was not there" are opposite.

They arrive as the same exception type. zendriver signals a MISS as a timeout —
`find(text=...)` raises asyncio.TimeoutError("Timeout (10s) waiting for any
element with text: ...") when nothing matches — so reporting every
asyncio.TimeoutError as the tool's own budget turns "no element matched" into
"the tool was too slow".

Observed on a real session: a click that returned in 10.4s reported "exceeded its
25s time budget". The caller read that as a slow browser and spent several turns
retrying, guessing URLs and falling back to curl. The element was simply absent,
which zendriver had said clearly and which this had thrown away.
"""

from __future__ import annotations

import asyncio

from src.errors import ToolTimeoutError, ZendriverMCPError
from src.tools.base import _timeout_error


def test_an_inner_timeout_keeps_its_own_message() -> None:
    inner = asyncio.TimeoutError("Timeout (10s) waiting for any element with text: 'Buy now'")
    err = _timeout_error("click", budget=25.0, elapsed=10.4, exc=inner)
    assert not isinstance(err, ToolTimeoutError)
    assert isinstance(err, ZendriverMCPError)
    assert "waiting for any element with text: 'Buy now'" in str(err)
    # And says plainly that the budget is not the explanation.
    assert "not the tool's 25s budget" in str(err)
    assert "10.4s" in str(err)


def test_a_real_budget_overrun_is_still_reported_as_one() -> None:
    err = _timeout_error("navigate", budget=25.0, elapsed=25.0, exc=asyncio.TimeoutError())
    assert isinstance(err, ToolTimeoutError)
    assert "25s time budget" in str(err)


def test_the_boundary_favours_the_budget_when_it_is_genuinely_close() -> None:
    # Just under the wire still counts as the budget: the outer wait_for cannot
    # fire early, so an elapsed time at the budget is definitionally ours.
    assert isinstance(_timeout_error("x", 25.0, 24.9, asyncio.TimeoutError()), ToolTimeoutError)
    # Comfortably under is not.
    assert not isinstance(_timeout_error("x", 25.0, 5.0, asyncio.TimeoutError()), ToolTimeoutError)


def test_an_inner_timeout_with_no_message_still_explains_itself() -> None:
    err = _timeout_error("click", 25.0, 2.0, asyncio.TimeoutError())
    assert "an internal operation timed out" in str(err)
    assert "not the tool's" in str(err)
