"""Link targets are available, and off by default.

An agent was asked to open an article it could see in the interaction tree. The
tree gave it an id and the label "[a]" — the anchor had no text — and no way to
learn where the link went. It clicked by text (timed out), invented three URL
slugs (all 404), fell back to curl, and hit a UA block that looked like a fourth
404. The href had been in the DOM the whole time.

Off by default because it is not free: measured on that same 125-link search
page, emitting every target grew the tree by 116%, and by 82% after same-origin
links were shortened to a path. On a 32k window that is 1.5-2k tokens for
information most trees do not need, so the caller asks for it.
"""

from __future__ import annotations

import asyncio
import json

from src.tools.content import ContentTools


def _tools(tree):
    tools = ContentTools.__new__(ContentTools)

    async def run_js(_script: str):
        # A fresh copy each call: the tool mutates what the walker returns, and a
        # test that shared one list would pass for the wrong reason.
        return json.loads(json.dumps(tree))

    tools.run_js = run_js  # type: ignore[method-assign]
    return tools


TREE = [
    {"id": 1, "t": "link", "l": "[a]", "r": "main", "h": "/2026/08/15/the-article/"},
    {"id": 2, "t": "link", "l": "Home", "r": "nav", "h": "https://other.example/"},
    {"id": 3, "t": "btn", "l": "Search", "r": "hdr"},
]


def test_off_by_default_costs_the_caller_nothing() -> None:
    out = asyncio.run(_tools(TREE).get_interaction_tree())
    parsed = json.loads(out)
    assert all("h" not in element for element in parsed)
    # And the rest of the entry is untouched.
    assert parsed[0]["l"] == "[a]"
    assert parsed[2]["t"] == "btn"


def test_links_true_returns_the_targets() -> None:
    parsed = json.loads(asyncio.run(_tools(TREE).get_interaction_tree(links=True)))
    assert parsed[0]["h"] == "/2026/08/15/the-article/"
    assert parsed[1]["h"] == "https://other.example/"
    # A button has no target and must not grow one.
    assert "h" not in parsed[2]


def test_the_unlabelled_link_is_the_one_this_exists_for() -> None:
    # "[a]" is what the failing session saw. With links=true it is followable.
    parsed = json.loads(asyncio.run(_tools(TREE).get_interaction_tree(links=True)))
    unlabelled = next(e for e in parsed if e["l"] == "[a]")
    assert unlabelled["h"].endswith("/the-article/")


def test_the_limit_still_applies_with_links_on() -> None:
    out = asyncio.run(_tools(TREE).get_interaction_tree(limit=1, links=True))
    assert out.startswith("[showing 1 of 3 elements")
    assert '"h"' in out


def test_a_non_dict_element_cannot_break_the_strip() -> None:
    # The walker is JS and validates nothing; a malformed entry must not throw
    # in the middle of returning a page.
    out = asyncio.run(_tools([{"id": 1, "t": "link", "h": "/x"}, "junk", None]).get_interaction_tree())
    assert '"h"' not in out
