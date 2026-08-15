"""Table-driven tests for the JS-escaping helper.

`escape_js_string` is the last line of defence against JS injection when
interpolating into inline JS templates. Known limits (semicolons, backticks,
</script>) are documented elsewhere; these tests lock down the things we
*do* escape so a refactor can't silently drop a case.
"""

from __future__ import annotations

import pytest

from src.tools.base import ToolBase


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("plain", "plain"),
        (r'say "hi"', r"say \"hi\""),
        ("it's fine", r"it\'s fine"),
        ("back\\slash", r"back\\slash"),
        ("line\nbreak", r"line\nbreak"),
        ("carriage\rreturn", r"carriage\rreturn"),
        # Double-quotes + single-quotes + backslashes co-exist cleanly.
        (r"""mix "a" 'b' \ok""", r"""mix \"a\" \'b\' \\ok"""),
    ],
)
def test_escape_js_string(raw: str, expected: str) -> None:
    assert ToolBase.escape_js_string(raw) == expected


def test_backslash_escaped_first() -> None:
    # The order matters: we must replace ``\`` with ``\\`` before touching
    # ``"`` or ``'``, otherwise the later replacements would be doubly
    # escaped. Verify by walking the implementation on a tricky input.
    assert ToolBase.escape_js_string('\\"') == r"\\\""
