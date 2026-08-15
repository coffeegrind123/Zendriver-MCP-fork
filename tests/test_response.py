"""Tests for the ToolResponse envelope."""

from __future__ import annotations

from src.response import ToolResponse


def test_summary_only_serialises_to_single_key() -> None:
    assert ToolResponse("done").to_dict() == {"summary": "done"}


def test_data_and_files_included_when_present() -> None:
    r = ToolResponse("saved", data={"count": 3}, files=["/tmp/x"])
    assert r.to_dict() == {
        "summary": "saved",
        "data": {"count": 3},
        "files": ["/tmp/x"],
    }


def test_empty_data_and_files_omitted() -> None:
    r = ToolResponse("ok", data={}, files=[])
    assert r.to_dict() == {"summary": "ok"}
