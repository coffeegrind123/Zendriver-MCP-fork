"""An empty get_text_content must say WHY it is empty.

From the same failing session as test_navigate_settle.py. The read returned:

    [chars 0-0 of 0]

and nothing else. That is indistinguishable from a page with genuinely no text,
so the agent concluded the site was behind a consent wall and started hunting
for a different news source. The real cause was that nothing had rendered yet.

"Not ready", "blocked/blank", and "text is there but not in innerText" have
three different right answers, and a bare zero picks none of them.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.tools.content import ContentTools


def _tools(js_result: Any, text: Any = "") -> ContentTools:
    tools = ContentTools.__new__(ContentTools)
    calls: list[str] = []

    async def run_js(script: str) -> Any:
        calls.append(script)
        if "innerText" in script:
            return text
        if isinstance(js_result, Exception):
            raise js_result
        return js_result

    tools.run_js = run_js  # type: ignore[method-assign]
    tools._calls = calls  # type: ignore[attr-defined]
    return tools


def test_non_empty_text_is_returned_untouched_and_costs_no_extra_probe() -> None:
    tools = _tools({}, text="hello world")
    out = asyncio.run(tools.get_text_content(max_chars=100, offset=0))
    assert out == "[chars 0-11 of 11]\nhello world"
    assert len(tools._calls) == 1, "the diagnostic must not run on the happy path"


def test_still_loading_says_so_and_names_the_fix() -> None:
    tools = _tools(
        {
            "readyState": "loading",
            "url": "https://www.bbc.com/news",
            "hasBody": True,
            "htmlChars": 500,
        }
    )
    out = asyncio.run(tools.get_text_content())
    assert out.startswith("[chars 0-0 of 0]")
    assert "still loading" in out
    assert "readyState=loading" in out
    assert "wait_for_network" in out
    assert "https://www.bbc.com/news" in out


def test_markup_present_but_no_text_points_at_iframes_and_shadow_roots() -> None:
    tools = _tools(
        {"readyState": "complete", "url": "https://x.test/", "hasBody": True, "htmlChars": 90_000}
    )
    out = asyncio.run(tools.get_text_content())
    assert "has markup" in out
    assert "iframe" in out
    assert "get_content" in out


def test_a_genuinely_blank_page_is_reported_as_blank() -> None:
    tools = _tools(
        {"readyState": "complete", "url": "https://x.test/", "hasBody": True, "htmlChars": 120}
    )
    out = asyncio.run(tools.get_text_content())
    assert "really is blank" in out


def test_missing_body_is_its_own_answer() -> None:
    tools = _tools(
        {"readyState": "complete", "url": "https://x.test/", "hasBody": False, "htmlChars": 300}
    )
    out = asyncio.run(tools.get_text_content())
    assert "no body element" in out


def test_a_failing_diagnostic_never_replaces_the_result_with_an_error() -> None:
    tools = _tools(RuntimeError("target closed"))
    out = asyncio.run(tools.get_text_content())
    assert out.startswith("[chars 0-0 of 0]"), "the pagination contract must survive"
    assert "could not diagnose" in out
    assert "target closed" in out


def test_a_non_dict_probe_result_is_handled() -> None:
    tools = _tools("undefined")
    out = asyncio.run(tools.get_text_content())
    assert "did not answer a diagnostic query" in out


def test_none_text_does_not_crash_pagination() -> None:
    tools = _tools(
        {"readyState": "complete", "url": "https://x.test/", "hasBody": True, "htmlChars": 10},
        text=None,
    )
    out = asyncio.run(tools.get_text_content())
    assert out.startswith("[chars 0-0 of 0]")
