"""Pagination contract for get_content / get_text_content."""

from __future__ import annotations

from src.tools.content import ContentTools


def test_first_slice_reports_next_offset() -> None:
    text = "abcdefghij" * 3  # 30 chars
    out = ContentTools._paginate(text, 10, 0)
    header, body = out.split("\n", 1)
    assert header == "[chars 0-10 of 30] (next: offset=10)"
    assert body == "abcdefghij"


def test_final_slice_has_no_next() -> None:
    text = "abcdefghij" * 3
    out = ContentTools._paginate(text, 10, 20)
    header, body = out.split("\n", 1)
    assert header == "[chars 20-30 of 30]"
    assert body == "abcdefghij"


def test_offset_clamps_past_end() -> None:
    text = "abcdefghij" * 3
    out = ContentTools._paginate(text, 10, 999)
    assert out.startswith("[chars 30-30 of 30]")


def test_whole_text_when_max_exceeds_length() -> None:
    text = "abcdefghij" * 3
    out = ContentTools._paginate(text, 1000, 0)
    header, body = out.split("\n", 1)
    assert header == "[chars 0-30 of 30]"
    assert body == text


def test_max_chars_floored_to_one() -> None:
    out = ContentTools._paginate("hello", 0, 0)
    header, body = out.split("\n", 1)
    assert header == "[chars 0-1 of 5] (next: offset=1)"
    assert body == "h"
